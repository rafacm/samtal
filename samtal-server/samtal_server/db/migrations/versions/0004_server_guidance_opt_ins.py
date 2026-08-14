"""The two opt-ins for the guidance an MCP server ships about itself

Two columns on `mcp_servers`, both additive. The boolean is NOT NULL
with a database-level default of false rather than a nullable column
rescued in Python: a row written before this migration then reads its
opt-in as closed from the database itself, which is what "off by
default" has to mean for a trust decision. The prompt list is nullable,
where NULL is the "none" the model already means.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "mcp_servers",
        sa.Column(
            "use_server_instructions",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("mcp_servers", sa.Column("inject_prompts", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("mcp_servers", "inject_prompts")
    op.drop_column("mcp_servers", "use_server_instructions")
