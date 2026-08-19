"""Baseline domain configuration schema

Revision ID: 0001
Revises:
Create Date: 2026-08-11
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
        "providers",
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("api_key_env", sa.Text(), nullable=True),
        sa.Column("egress", sa.Boolean(), nullable=True),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("secrets", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("stage", "name", name="pk_providers"),
    )
    op.create_table(
        "mcp_servers",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("transport", sa.Text(), nullable=False),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("env", sa.JSON(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("headers", sa.JSON(), nullable=False),
        sa.Column("egress", sa.Boolean(), nullable=True),
        sa.Column("tool_timeout_s", sa.Float(), nullable=False),
        sa.Column("secrets", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("name", name="pk_mcp_servers"),
    )
    op.create_table(
        "agent_defaults",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("llm", sa.Text(), nullable=True),
        sa.Column("asr", sa.Text(), nullable=True),
        sa.Column("tts", sa.Text(), nullable=True),
        sa.Column("vad", sa.Text(), nullable=True),
        sa.Column("mcp", sa.JSON(), nullable=True),
        sa.Column("filler", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_agent_defaults"),
        # The singleton, enforced by the schema rather than by a
        # convention in the repository. Re-keying this table for
        # per-family defaults later is what Alembic is here for.
        sa.CheckConstraint("id = 'singleton'", name="ck_agent_defaults_singleton"),
    )
    op.create_table(
        "agents",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("llm", sa.Text(), nullable=True),
        sa.Column("asr", sa.Text(), nullable=True),
        sa.Column("tts", sa.Text(), nullable=True),
        sa.Column("vad", sa.Text(), nullable=True),
        sa.Column("mcp", sa.JSON(), nullable=True),
        sa.Column("filler", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("name", name="pk_agents"),
    )
    op.create_table(
        "devices",
        sa.Column("mac", sa.Text(), nullable=False),
        sa.Column("agents", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("mac", name="pk_devices"),
    )
    op.create_table(
        "domain_settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_domain_settings"),
    )


def downgrade() -> None:
    op.drop_table("domain_settings")
    op.drop_table("devices")
    op.drop_table("agents")
    op.drop_table("agent_defaults")
    op.drop_table("mcp_servers")
    op.drop_table("providers")
