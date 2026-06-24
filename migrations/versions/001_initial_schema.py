"""Initial schema: experiments and candidates tables.

Revision ID: 001
Revises:
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("recipe_name", sa.String(255), nullable=False),
        sa.Column("target_name", sa.String(500), nullable=False),
        sa.Column("target_pdb_id", sa.String(20), nullable=True),
        sa.Column("source_filename", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "candidates",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "experiment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_id", sa.String(255), nullable=False),
        sa.Column("fasta_sequence", sa.Text, nullable=False),
        sa.Column("binding_affinity_kd", sa.Float, nullable=True),
        sa.Column("humanness_score", sa.Float, nullable=True),
        sa.Column("aggregation_propensity", sa.Float, nullable=True),
        sa.Column("annotation", sa.Text, nullable=True),
        sa.Column("raw_scores", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_index("ix_candidates_experiment_id", "candidates", ["experiment_id"])
    op.create_index("ix_candidates_binding_affinity_kd", "candidates", ["binding_affinity_kd"])
    op.create_index("ix_candidates_humanness_score", "candidates", ["humanness_score"])
    op.create_index("ix_candidates_aggregation_propensity", "candidates", ["aggregation_propensity"])


def downgrade() -> None:
    op.drop_table("candidates")
    op.drop_table("experiments")
