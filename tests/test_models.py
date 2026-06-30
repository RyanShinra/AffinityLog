"""Model-layer smoke tests — no database required.

These assert the *shape* of the ORM metadata (tables, the Postgres-specific constraint flags,
the value-type vocabulary). They guard the design decisions made during the schema-first redesign;
full behavioral coverage (does NULLS NOT DISTINCT actually reject a dup? does the GIN index get
used?) belongs in a testcontainers integration test, a follow-up to this PR.
"""

from app.database import ModelBase
from app.models import orm

EXPECTED_TABLES = {
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
}


def test_all_tables_register() -> None:
    assert set(ModelBase.metadata.tables) == EXPECTED_TABLES


def test_metric_identity_is_nulls_not_distinct() -> None:
    """The (module, column_key, variant_kind, variant) unique key must treat NULLs as equal,
    so a metric with no variant stays unique per (module, column_key)."""
    metrics = ModelBase.metadata.tables["metrics"]
    constraint = next(c for c in metrics.constraints if c.name == "uq_metric_identity")
    assert constraint.dialect_options["postgresql"]["nulls_not_distinct"] is True


def test_candidate_scores_has_gin_index() -> None:
    """The JSONB scores bag must be GIN-indexed so we can query inside it."""
    candidates = ModelBase.metadata.tables["candidates"]
    index = next(ix for ix in candidates.indexes if ix.name == "ix_candidates_scores_gin")
    assert index.dialect_options["postgresql"]["using"] == "gin"


def test_metric_value_type_excludes_artifact() -> None:
    """Files (structures, sequences) are Artifacts, not Metrics — ARTIFACT must stay out of the
    value-type vocabulary so `Metric` only catalogs orderable/filterable outputs."""
    assert "ARTIFACT" not in {member.name for member in orm.MetricValueType}
