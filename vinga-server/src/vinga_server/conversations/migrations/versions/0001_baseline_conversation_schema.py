"""Baseline conversation store schema

The four tables of `conversations.db`: the session spine, the turn
timeline, the tool invocations a turn issued, and the decision track
underneath them.

`sqlite_autoincrement=True` on the three cursor tables is load-bearing
rather than decorative, and is the reason this is written as
`op.create_table` with the option rather than as plain DDL: without it
SQLite reuses the deleted maximum rowid, and retention deletes from
exactly the end a cursor points past.

Revision ID: 0001
Revises:
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session", sa.Text(), nullable=False),
        sa.Column("device", sa.Text(), nullable=True),
        sa.Column("client", sa.Text(), nullable=True),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column("agents", sa.JSON(), nullable=True),
        sa.Column("protocol", sa.Text(), nullable=True),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("closed_at", sa.Text(), nullable=True),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column("close_reason", sa.Text(), nullable=True),
        sa.Column("server_version", sa.Text(), nullable=True),
        sa.Column("revision", sa.Text(), nullable=True),
        sa.Column("providers", sa.JSON(), nullable=True),
        sa.Column("metrics", sa.Boolean(), nullable=False),
        sa.Column("text", sa.Boolean(), nullable=False),
        sa.Column("dropped", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
        sa.UniqueConstraint("session", name="uq_sessions_session"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_sessions_device", "sessions", ["device"])
    op.create_index("ix_sessions_started_at", "sessions", ["started_at"])
    op.create_table(
        "turns",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session", sa.Text(), nullable=False),
        sa.Column("t_ms", sa.Integer(), nullable=False),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column("heard", sa.Text(), nullable=True),
        sa.Column("heard_duration_s", sa.Float(), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("language_confidence", sa.Float(), nullable=True),
        sa.Column("reply", sa.Text(), nullable=True),
        sa.Column("legs", sa.JSON(), nullable=True),
        sa.Column("asr_ms", sa.Integer(), nullable=True),
        sa.Column("first_token_ms", sa.Integer(), nullable=True),
        sa.Column("llm_ms", sa.Integer(), nullable=True),
        sa.Column("tts_first_audio_ms", sa.Integer(), nullable=True),
        sa.Column("rounds", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_turns"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_turns_session", "turns", ["session", "id"])
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("turn", sa.Integer(), nullable=False),
        sa.Column("session", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("entry", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("malformed", sa.Boolean(), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("is_error", sa.Boolean(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_tool_invocations"),
        # The closed set, enforced by the schema rather than only by the
        # classifier that fills it, because the whole value of the
        # column is that a query may enumerate it.
        sa.CheckConstraint(
            "source in ('builtin', 'device', 'mcp', 'unknown')",
            name="ck_tool_invocations_source",
        ),
    )
    op.create_index("ix_tool_invocations_session", "tool_invocations", ["session"])
    op.create_index("ix_tool_invocations_turn", "tool_invocations", ["turn"])
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session", sa.Text(), nullable=False),
        sa.Column("t_ms", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_events_session", "events", ["session", "id"])


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("tool_invocations")
    op.drop_table("turns")
    op.drop_table("sessions")
