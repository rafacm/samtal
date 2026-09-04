"""Drop the provider secret slots named max_tokens

A data migration and nothing else: no table, column, index or
constraint moves, so there is nothing here for autogenerate to have
written and this file is hand-written whole.

`max_tokens` was secret-shaped to the option heuristic, which is the
defect #277 fixes, and the slot check read the same predicate. So
`vinga provider secret set <stage> <entry> max_tokens` was accepted and
`set_secret` stored an envelope under that slot, in a row nothing ever
consumed: no read, no build and no request has ever looked a provider's
`max_tokens` up among its stored secrets. With the exemption in place
the slot is refused, and a row left behind would be worse than useless.
It is loaded and verified at every boot, it is listed as stored-secret
metadata, and `vinga export` renders it into the foot of its document
as a `provider secret set ... max_tokens` command that the import path
the same document prescribes now refuses, which breaks the documented
export-and-reapply recovery on a deployment that never asked for any of
it.

So the slot is deleted, once, on the way into this release. Deleting
loses nothing that was ever read, which is the whole warrant for a
forward migration with no way back: there is no plaintext here to
preserve and no reader to preserve it for.

Exactly that key and nothing else. The `secrets` column is JSON rather
than JSONB, so both halves cast to compare and to subtract, and the
`where` keeps the statement off every row that has no such slot, which
is every row on almost every deployment: an untouched row keeps its
column's bytes exactly as they were written, rather than being rewritten
through a JSON round trip that would reorder its remaining keys. The
sibling slots of an affected row survive by construction, since `-`
removes one key and copies the rest.

MCP servers are deliberately not touched. Their slots are dotted
`env.` and `headers.` paths read against the wider undeclared-name rule,
which #277 does not narrow, so no `max_tokens` slot there was ever
reachable through the predicate this migration cleans up after.

Revision ID: 3002_drop_max_tokens_secrets
Revises: 3001_postgres_domain
Create Date: 2026-09-04
"""

from collections.abc import Sequence

from alembic import op

revision: str = "3002_drop_max_tokens_secrets"
down_revision: str | None = "3001_postgres_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The one slot name this migration removes, written once and read by
# both halves of the statement below.
SLOT = "max_tokens"


def upgrade() -> None:
    op.execute(
        f"update domain.providers "
        f"set secrets = (secrets::jsonb - '{SLOT}')::json "
        f"where jsonb_exists(secrets::jsonb, '{SLOT}')"
    )


def downgrade() -> None:
    """Nothing, and not for want of trying.

    A downgrade restores what an upgrade changed, and what this one
    changed is gone: the ciphertext is deleted, no other row or column
    holds a copy, and an empty envelope invented here would fail the
    next boot's verification rather than restore anything. An older
    image meeting a database this ran on finds a provider with one fewer
    stored secret, which is a slot it never read either.
    """
