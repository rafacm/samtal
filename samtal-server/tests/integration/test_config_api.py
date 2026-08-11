"""The configuration API over a real socket.

The unit suite drives the same routes through an injected test client,
which is the right seam for the acceptance suite and cannot show that an
answer arrives at all rather than being cut short by the client's own
timeouts. Only a real connection and a real lock can demonstrate that.
"""

import os
import sqlite3
from pathlib import Path

import pytest

from samtal_server import db as db_module
from samtal_server.config import cli
from samtal_server.db import DATABASE_FILENAME


def _token() -> str:
    return os.environ["SAMTAL_API_SECRET"]


def test_a_contended_write_answers_over_a_real_socket(
    served_api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retryable refusal through the client the CLI actually builds,
    over a real connection, with a real lock held.

    The unit suite forces the same 409 through an injected test client,
    which cannot show what this one does: that the answer arrives rather
    than being cut short by the client's own read timeout. The thresholds
    here are deliberately short so the test finishes; that the production
    read timeout outlasts the production busy timeout is asserted
    directly in the unit suite, where nothing is shortened."""
    directory = tmp_path / "db"
    monkeypatch.setattr(db_module, "BUSY_TIMEOUT_MS", 500)

    with served_api(directory) as api_url:
        # The API opens the database per request, so one read is what
        # creates the file this then takes the lock on.
        opener = cli.build_client(api_url, _token())
        try:
            assert opener.get("/config").status_code == 200
        finally:
            opener.close()

        holder = sqlite3.connect(directory / DATABASE_FILENAME, isolation_level=None)
        holder.execute("BEGIN IMMEDIATE")
        client = cli.build_client(api_url, _token())
        try:
            response = client.put("/agents/sam", json={"prompt": "You are Sam."})
        finally:
            client.close()
            holder.close()

        assert response.status_code == 409
        assert set(response.json()) == {"detail"}

        # And with the lock let go the same request is answered, which is
        # what makes the refusal above the retryable one it says it is.
        client = cli.build_client(api_url, _token())
        try:
            answered = client.put("/agents/sam", json={"prompt": "You are Sam."})
        finally:
            client.close()
        assert answered.status_code == 200
