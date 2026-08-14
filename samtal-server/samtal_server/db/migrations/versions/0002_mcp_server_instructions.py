"""Operator guidance on an MCP server entry

Additive and nullable: NULL is the unset the model already means, so
every row written before this reads as an entry with no guidance and
loads unchanged.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("mcp_servers", sa.Column("instructions", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("mcp_servers", "instructions")
