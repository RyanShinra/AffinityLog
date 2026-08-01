"""add metric notes

Revision ID: 006
Revises: 005
Create Date: 2026-08-01 16:23:08.375297

WHY
---
`display_name` says what a metric is called. Nothing in the schema said how it MISLEADS — and this
corpus proved that gap matters: `boltz2.protein_iptm` reads ~0.95 on a fold containing no antigen,
higher than any genuine HER2 complex, so a consumer sorting by it gets exactly the wrong answer.

Those warnings existed only as prose in `seed/catalog.json` and could not reach an API. This column
gives them a home, so the caveat travels with the number:

    "~0.95 observed. NOT binding: no antigen was in the fold."
    "0-1 in Boltz2 but 0-100 in ColabFold/ESMFold - never compare across modules."
    "Assembly-wide, so 2-chain and 3-chain folds are not strictly comparable even within a variant."

Nullable on purpose. Only ~26 of the 138 metric identities in the corpus are curated; the rest carry
no note rather than an invented one, and `provenance` records which is which.

Written by `alembic revision --autogenerate` from the model change — the first migration here to
come out of autogenerate rather than by hand, which is what it is for. It also correctly proposed
NOTHING for the candidate_summary view, confirming that keeping app.models.views out of env.py's
imports does what migration 004's docstring claims.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: str | Sequence[str] | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the nullable notes column to metrics."""
    op.add_column("metrics", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    """Drop the notes column.

    Unlike 005's enum value, a column CAN be dropped cleanly — so this downgrade is real, not a
    no-op. It does discard whatever curated text is in the column; that text's source of truth is
    seed/catalog.json, so re-running the seeder after a re-upgrade restores it.
    """
    op.drop_column("metrics", "notes")
