"""Shared prompt fragments, and the lists that include them

One new table and one nullable column on each of the two layer tables.
Additive throughout: a database written before this has no fragments and
no includes, which is exactly what a NULL column and an empty table say,
so every row that was there loads unchanged.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prompt_fragments",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("name", name="pk_prompt_fragments"),
    )
    for table in ("agent_defaults", "agents"):
        op.add_column(table, sa.Column("prompt_includes", sa.JSON(), nullable=True))


def downgrade() -> None:
    for table in ("agents", "agent_defaults"):
        op.drop_column(table, "prompt_includes")
    op.drop_table("prompt_fragments")
