"""rename enum 'chain' to 'role', along with column renaming in the candidate chains table

Revision ID: 007
Revises: 006
Create Date: 2026-08-07 16:10:37.237454

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: str | Sequence[str] | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Rename the column and its enum type. Hand-written — autogenerate cannot produce this.

    Alembic has `alter_column(new_column_name=...)` for the column but no operation for renaming an
    enum TYPE, hence the raw `op.execute`. Renaming beats dropping and recreating: it updates one row
    in `pg_type`, with no data rewrite and no need to drop the columns that depend on the type first.

    `candidate_summary` needs no attention here even though its CASE reads this column. Postgres
    stores view definitions as parse trees that reference columns by attribute number rather than by
    name, so the rename propagates into the view automatically. (`sql/candidate_summary.sql` is a
    separate matter — that file is text, so it was updated by hand alongside this migration.)

    Order is irrelevant: a column references its type by OID, so neither rename disturbs the other.
    """
    op.alter_column(table_name="candidate_chains", column_name="chain", new_column_name="role")
    op.execute("ALTER TYPE chain RENAME TO chainrole")


def downgrade() -> None:
    """Exact mirror. Renames are lossless in both directions, so this genuinely round-trips —
    unlike migration 005, whose `ALTER TYPE ... ADD VALUE` cannot be undone at all."""
    op.alter_column(table_name="candidate_chains", column_name="role", new_column_name="chain")
    op.execute("ALTER TYPE chainrole RENAME TO chain")
