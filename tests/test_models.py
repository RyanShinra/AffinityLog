"""Model-layer smoke tests — no database required.

These assert the *shape* of the ORM metadata (tables, the Postgres-specific constraint flags,
the value-type vocabulary). They guard the design decisions made during the schema-first redesign;
full behavioral coverage (does NULLS NOT DISTINCT actually reject a dup? does the GIN index get
used?) belongs in a testcontainers integration test, a follow-up to this PR.
"""

from sqlalchemy import Constraint, ForeignKey, Table

from app.database import ModelBase
from app.models import orm

EXPECTED_TABLES: set[str] = {
    "modules",
    "metrics",
    "concepts",
    "recipes",
    "recipe_modules",
    "projects",
    "targets",
    "experiments",
    "candidates",
    "artifacts",
    "benchmark_datasets",
    "benchmark_results",
    "candidate_chains",
}


def test_all_tables_register() -> None:
    assert set(ModelBase.metadata.tables) == EXPECTED_TABLES


def test_metric_identity_is_nulls_not_distinct() -> None:
    """The (module, column_key, variant_kind, variant) unique key must treat NULLs as equal,
    so a metric with no variant stays unique per (module, column_key)."""
    metrics: Table = ModelBase.metadata.tables["metrics"]
    constraint: Constraint = next(c for c in metrics.constraints if c.name == "uq_metric_identity")
    assert constraint.dialect_options["postgresql"]["nulls_not_distinct"] is True


def test_candidate_scores_has_gin_index() -> None:
    """The JSONB scores bag must be GIN-indexed so we can query inside it."""
    candidates: Table = ModelBase.metadata.tables["candidates"]
    index = next(ix for ix in candidates.indexes if ix.name == "ix_candidates_scores_gin")
    assert index.dialect_options["postgresql"]["using"] == "gin"


def test_metric_value_type_excludes_artifact() -> None:
    """Files (structures, sequences) are Artifacts, not Metrics — ARTIFACT must stay out of the
    value-type vocabulary so `Metric` only catalogs orderable/filterable outputs."""
    assert "ARTIFACT" not in {member.name for member in orm.MetricValueType}


def _foreign_key_on(table: Table, column_name: str) -> ForeignKey:
    """The FK *held by* `column_name` on `table` (fk.parent), not the column it points at."""
    fk: ForeignKey | None = next(
        (fk for fk in table.foreign_keys if fk.parent.name == column_name), None
    )
    assert fk is not None, f"no foreign key on column {table.name}.{column_name}"
    return fk


def test_transform_lineage_fk_is_self_referential_set_null() -> None:
    """A transformed Metric points at its raw sibling; deleting the raw one must NULL the
    lineage link, not cascade-delete the transformed metric (which has its own benchmarks)."""

    # ALTER TABLE metrics ADD CONSTRAINT fk_metrics_transform_of_metric_id
    #   FOREIGN KEY (transform_of_metric_id) REFERENCES metrics(id) ON DELETE SET NULL;

    metrics_table: Table = ModelBase.metadata.tables["metrics"]

    foreign_key: ForeignKey = _foreign_key_on(metrics_table, "transform_of_metric_id")
    # Asserts that the transformed table refers to the raw metrics table
    assert foreign_key.column.table.name == "metrics"
    assert foreign_key.ondelete == "SET NULL"


def test_candidate_chains_cascade_from_candidate() -> None:
    """Chains are owned by their candidate — deleting the candidate must take them with it."""
    chains_table: Table = ModelBase.metadata.tables["candidate_chains"]

    foreign_key: ForeignKey = _foreign_key_on(chains_table, "candidate_id")
    assert foreign_key.column.table.name == "candidates"
    assert foreign_key.ondelete == "CASCADE"
