"""
ORM models for AffinityLog.

Convention (per project preference): wherever a column does something Postgres-specific
(JSONB, arrays, GIN indexes, partial/null-distinct uniqueness), the raw SQL equivalent is in a
comment so the ORM stays a convenience, not a black box.

SQLAlchemy 2.0 ↔ what you already know:
  Mapped[str]         → a NOT NULL column            (C#: `string`)
  Mapped[str | None]  → a NULLABLE column            (C#: `string?`)
  mapped_column(...)  → the column definition        (EF Core: fluent config / [Column])
  relationship(...)   → a Python-side nav property, NO column (EF: navigation property)
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    INTEGER,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.catalog.identifiers import ColumnKey, ModuleName, SequenceId, VariantName
from app.catalog.variant_kind import VariantKind
from app.database import ModelBase

# ---------------------------------------------------------------------------
# Enums (controlled vocabularies). Each becomes a native Postgres ENUM type.
# ---------------------------------------------------------------------------
# NOTE: SAEnum binds the member .name (not .value) — Postgres enum labels are the
# UPPERCASE names. Any hand-written `ALTER TYPE ... ADD VALUE` must use the name.
#
# NOT AN EXHAUSTIVE LIST. `VariantKind` is a native Postgres ENUM too, but it lives in
# `app/catalog/variant_kind.py` — the catalog owns that vocabulary and this module only
# stores it. It is imported above and used by `Metric.variant_kind` below.
# ---------------------------------------------------------------------------


class ModuleType(enum.Enum):
    DESIGN = "design"
    SCORE = "score"
    DESIGN_AND_SCORE = "design_and_score"


class ModuleFunction(enum.Enum):
    DE_NOVO_DESIGN = "de_novo_design"
    DIRECTED_EVOLUTION = "directed_evolution"
    FOLDING_LIKELIHOOD = "folding_likelihood"
    BINDING_PREDICTION = "binding_prediction"
    DEVELOPABILITY = "developability"
    AGGREGATION = "aggregation"
    STABILITY = "stability"
    IMMUNOGENICITY = "immunogenicity"
    HYDROPHOBICITY = "hydrophobicity"
    EXPRESSION = "expression"
    EVOLUTIONARY_LIKELIHOOD = "evolutionary_likelihood"
    POLYREACTIVITY = "polyreactivity"


class MetricValueType(enum.Enum):
    FLOAT = "float"
    INT = "int"
    BOOL = "bool"
    CATEGORICAL = "categorical"


class Direction(enum.Enum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
    NEUTRAL = "neutral"


class Provenance(enum.Enum):
    INFERRED = "inferred"
    AWS_CONFIRMED = "aws_confirmed"


# ---------------------------------------------------------------------------
# Association table: Recipe <-> Module (the one many-to-many).
# A recipe IS its composition of modules; defined before the classes that use it.
#   CREATE TABLE recipe_modules (
#       recipe_id UUID REFERENCES recipes(id) ON DELETE CASCADE,
#       module_id UUID REFERENCES modules(id) ON DELETE CASCADE,
#       PRIMARY KEY (recipe_id, module_id));
# ---------------------------------------------------------------------------

recipe_modules = Table(
    "recipe_modules",
    ModelBase.metadata,
    Column(
        "recipe_id",
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "module_id",
        UUID(as_uuid=True),
        ForeignKey("modules.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


# ===========================================================================
# CATALOG SIDE (seeded reference data)
# ===========================================================================


class Module(ModelBase):
    """
    An algorithmic unit in Bio Discovery (e.g. "Boltz2", "PLM Pseudo-Perplexity").

    *** This is the fully-commented REFERENCE model — copy these patterns. ***
    """

    __tablename__ = "modules"

    # UUID primary key. `Mapped[uuid.UUID]` is the Python-side type; `mapped_column(...)` is the
    # column. default=uuid.uuid4 means Python generates the id (vs server_default for DB-side).
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Non-Optional Mapped[str] → NOT NULL. unique=True → a UNIQUE index on the column.
    name: Mapped[ModuleName] = mapped_column(String(128), unique=True)

    # Enum → a native Postgres ENUM type.  SQL: module_type moduletype NOT NULL
    module_type: Mapped[ModuleType] = mapped_column(SAEnum(ModuleType))

    # Postgres native ARRAY of enum. Later we query it with array operators, e.g.
    #   SELECT * FROM modules WHERE functions && ARRAY['stability']::modulefunction[];
    functions: Mapped[list[ModuleFunction]] = mapped_column(ARRAY(SAEnum(ModuleFunction)), default=list)

    # The `| None` is what makes it NULLABLE (like C#'s `string?`).
    repo_url: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)

    # From the Module Evaluation scrape. version e.g. "1.0"; license e.g. "MIT License" /
    # "BSD 3-Clause License" — varies per module, no project-wide default.
    version: Mapped[str | None] = mapped_column(String(64))
    license: Mapped[str | None] = mapped_column(String(128))

    # server_default=func.now() → the DATABASE writes the timestamp (DEFAULT now()), not Python.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships — Python-side nav properties, NOT columns.
    # One Module → many Metric. back_populates wires both sides; cascade deletes its metrics with it.
    metrics: Mapped[list["Metric"]] = relationship(back_populates="module", cascade="all, delete-orphan")
    # Other side of the M2M; `secondary` points at the association table above.
    recipes: Mapped[list["Recipe"]] = relationship(secondary=recipe_modules, back_populates="modules")


class Metric(ModelBase):
    """One named output a Module can emit (a column). The catalog's core — all our refinements live here."""

    __tablename__ = "metrics"
    __table_args__ = (
        # Metric identity is the natural key (module_id, column_key, variant_kind, variant) — NOT
        # column_key alone, because the same column name recurs across modules/runs meaning different
        # things, and the variant is what tells those apart.
        # NULLS NOT DISTINCT (Postgres 15+) is the subtle bit: by default Postgres treats NULL != NULL,
        # so two rows with a NULL variant would NOT collide. We want them to — so a column with no
        # variant stays unique per (module, column_key). That common (variant IS NULL) case is exactly
        # what this guards.
        #   UNIQUE NULLS NOT DISTINCT (module_id, column_key, variant_kind, variant)
        UniqueConstraint(
            "module_id",
            "column_key",
            "variant_kind",
            "variant",
            name="uq_metric_identity",
            postgresql_nulls_not_distinct=True,
        ),
        # Both-or-neither. A row with exactly one of the pair populated is unreachable: `decompose()`
        # never emits a variant without a kind, and an INTERFACE row with a NULL variant can never
        # match (module, column_key, 'INTERFACE', <interface_kind>). It is dead data that also HIDES
        # things — `app/catalog/invariants.py` counts variant_kind IS NULL as a "bare" row, and a
        # bare row is what suppresses its unsatisfiable-axis branch, so one malformed row silenced
        # that check for the whole heading.
        #
        # Unlike the functional dependency (module_id, column_key) -> variant_kind, which no
        # constraint can express and which that module exists to check at write time, THIS one is a
        # plain CHECK. So it is enforced here rather than detected there — migration 008.
        #
        # Written as an equality of two IS NULL tests on purpose: a CHECK passes when its expression
        # is NULL and fails only on false, so a form that could evaluate to NULL would be a hole.
        # `IS NULL` yields true/false and never NULL, so both sides are real booleans.
        CheckConstraint(
            "(variant_kind IS NULL) = (variant IS NULL)",
            name="ck_metric_variant_pair",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    module_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("modules.id", ondelete="CASCADE"))
    column_key: Mapped[ColumnKey] = mapped_column(String(128))  # raw export header, e.g. "pseudo_perplexity"
    display_name: Mapped[str] = mapped_column(String(255))  # "ESM2 Perplexity"
    value_type: Mapped[MetricValueType] = mapped_column(SAEnum(MetricValueType))
    unit: Mapped[str | None] = mapped_column(String(32))  # "kcal/mol", "°C"; NULL = dimensionless
    direction: Mapped[Direction] = mapped_column(SAEnum(Direction), default=Direction.NEUTRAL)
    property_categories: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)

    # What disambiguates this column WITHIN its module. Some modules emit the same column_key more
    # than once, meaning a different thing each time; `variant_kind` says along WHICH axis it varies,
    # `variant` is the value on that axis. The pair is also the handle a GUI groups/compares by.
    #   PARAMETER     a config knob changed the run    PLM perplexity, model_type=esm  → variant="esm"
    #   SOURCE_MODEL  scored against another model      igdesign scRMSD via ABB3        → variant="ABB3"
    #   COMPONENT     one readout of a multi-part metric  thermostability Tm1/Tm2/Tm3   → variant="Tm1"
    #   MODE          a distinct run-mode of the same module
    # Both NULL = the common case: this column appears once, nothing to disambiguate.
    variant_kind: Mapped[VariantKind | None] = mapped_column(SAEnum(VariantKind))
    variant: Mapped[VariantName | None] = mapped_column(String(128))

    concept_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="SET NULL"))
    concept: Mapped["Concept | None"] = relationship(back_populates="metrics")

    # Benchmark normalization, e.g. {"type": "mahalanobis_gaussian", "params": {"mu": 3.59, "sigma": 7.47}}
    transform: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    provenance: Mapped[Provenance] = mapped_column(
        SAEnum(Provenance), default=Provenance.INFERRED, server_default=text("'INFERRED'")
    )

    # Curator's free text: the caveat a consumer needs BEFORE trusting the number. This is where the
    # hard-won warnings live — "~0.95 observed, but NOT binding: no antigen was in the fold", "0-1 in
    # Boltz2 but 0-100 in ColabFold", "assembly-wide, so 2-chain and 3-chain folds aren't comparable".
    # display_name says what a metric is called; `notes` says how it misleads. Seeded from
    # seed/catalog.json, which is why it is nullable — the ~112 uncurated metrics carry no note
    # rather than an invented one.
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    module: Mapped["Module"] = relationship(back_populates="metrics")
    # One Metric -> many BenchmarkResult (a metric can be validated against several developability
    # properties; see the Module Evaluation scrape). Mirrors the `metrics` relationship on Module.
    benchmark_results: Mapped[list["BenchmarkResult"]] = relationship(back_populates="metric", cascade="all, delete-orphan")

    # A TRANSFORM-variant Metric (see `app/catalog/variant_kind.py`) is a derived quantity of some
    # raw Metric, not an independent one — this is the lineage link back to its raw counterpart.
    # Only set on the transformed sibling.
    transform_of_metric_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("metrics.id", ondelete="SET NULL")
    )
    transform_of: Mapped["Metric | None"] = relationship(remote_side=[id])

    # A transform-correlation statistic: how much does the transform actually change the ranking?
    # NOT derivable from the Module Evaluation scrape (that only gives each metric's correlation
    # against DPBD properties, e.g. BenchmarkResult.spearman_correlation — never one metric's
    # values directly against its sibling's). Worth capturing: FastDPE's SFvCSP raw/transformed
    # pair has identical Spearman against Titer (0.199 both) but different AuROC (0.647 vs 0.353) —
    # proof two "duplicate-looking" metrics can diverge in ways Spearman alone hides. Only
    # computable once real candidate data has both the raw and transformed columns imported for
    # the same candidates, e.g. {"spearman_vs_raw": ..., "n": ..., "computed_at": ...} — lazily
    # computed post-import, not backfillable from the scrape. NULL until then, same pattern as
    # Metric.transform.
    transform_stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Concept(ModelBase):
    __tablename__ = "concepts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)

    metrics: Mapped[list["Metric"]] = relationship(back_populates="concept")


# ===========================================================================
# BenchmarkDataset + BenchmarkResult — from the Module Evaluation scrape.
#
# BenchmarkDataset: one row, describing the DPBD reference dataset every benchmark below is
# validated against. All fields nullable except name/description — this is documentation, not
# something the app writes to at runtime. `description` also carries the binarization methodology
# (the per-property positive-label thresholds used to compute AuROC/AuPRC/Precision@Top5%, e.g.
# "Titer > 0.1 mg/mL", "Aggregation (polydispersity idx) < median + 1.5*IQR", "Hydrophobicity:
# undefined — bimodal distribution, insufficient seed antibodies for a reliable variance estimate")
# — static global methodology prose, not per-row data, so it doesn't warrant its own table.
#


class BenchmarkDataset(ModelBase):
    """Describes the DPBD reference dataset used to validate the model."""

    __tablename__ = "benchmark_datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True)  # "DPBD"
    version: Mapped[str | None] = mapped_column(String(32))

    # Page-level DPBD paragraph, including the binarization methodology (Titer > 0.1 mg/mL,
    # median ± 1.5*IQR for others, undefined for Hydrophobicity) — static prose, not per-row data.
    description: Mapped[str] = mapped_column(Text)

    seed_antibody_count: Mapped[int | None] = mapped_column(INTEGER)
    antigen_count: Mapped[int | None] = mapped_column(INTEGER)
    max_variants_per_seed: Mapped[int | None] = mapped_column(INTEGER)
    mutation_strategy_count: Mapped[int | None] = mapped_column(INTEGER)
    assay_count: Mapped[int | None] = mapped_column(INTEGER)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Optional convenience side of the FK on BenchmarkResult.benchmark_dataset_id — not required
    # (that FK works fine without it), added for symmetry with every other relationship in this
    # file. Needs the matching back_populates added to BenchmarkResult.benchmark_dataset below.
    results: Mapped[list["BenchmarkResult"]] = relationship(back_populates="benchmark_dataset")


# BenchmarkResult: one row per (metric, property) pair — a Metric can be validated against
# several developability properties (see Metric.benchmark_results above), each with its own
# stats. Fields:
#   id, metric_id (FK -> metrics.id, ondelete="CASCADE"),
#   benchmark_dataset_id (FK -> benchmark_datasets.id, ondelete="SET NULL", nullable),
#   property (str — e.g. "Thermostability (Tm1)", "Aggregation (Polydispersity idx)"; plain
#     String not an enum, since the scrape isn't confirmed to have the full closed vocabulary),
#   n (int), spearman_correlation (float), spearman_significance (str | None — the "***"),
#   auroc / auprc / precision_top5 (float | None — NULL here is not always "not scraped yet":
#     Hydrophobicity rows are NULL by AWS's own design, since no binarization threshold is
#     defined for it (bimodal distribution) — don't treat that NULL as missing data to chase),
#   precision_top5_null_dist (ARRAY(Float) | None — [p5, p95] from AWS's 100-permutation null
#     model, same ARRAY pattern as Metric.property_categories above for a small fixed-size array),
#   positive_ratio (float | None),
#   strata (JSONB | None — the per-antibody-format sub-breakdown: VHH/IgG/ScFv/Near-Germline IgG,
#     each with its own n + spearman; nest it here rather than a further child table since it's
#     inherently a sub-structure of one benchmark row, not independently queryable),
#   created_at.
#
# Relationships: BenchmarkResult.metric back_populates Metric.benchmark_results;
# BenchmarkResult.benchmark_dataset back_populates BenchmarkDataset.results.
# ===========================================================================
class BenchmarkResult(ModelBase):
    """The result of benchmarking a metric for a particular model"""

    __tablename__ = "benchmark_results"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    metric_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("metrics.id", ondelete="CASCADE"), index=True)

    benchmark_dataset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("benchmark_datasets.id", ondelete="SET NULL")
    )

    property: Mapped[str] = mapped_column(String(255))
    n: Mapped[int] = mapped_column(INTEGER)
    spearman_correlation: Mapped[float] = mapped_column(Float())
    spearman_significance: Mapped[str | None] = mapped_column(String(8))
    auroc: Mapped[float | None] = mapped_column(Float())
    auprc: Mapped[float | None] = mapped_column(Float())
    precision_top5: Mapped[float | None] = mapped_column(Float())
    precision_top5_null_dist: Mapped[list[float] | None] = mapped_column(ARRAY(Float()))
    positive_ratio: Mapped[float | None] = mapped_column(Float())
    strata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    metric: Mapped["Metric"] = relationship(back_populates="benchmark_results")
    benchmark_dataset: Mapped["BenchmarkDataset | None"] = relationship(back_populates="results")


# ===========================================================================
# EXPERIMENT SIDE (ingested data)
# ===========================================================================


class Project(ModelBase):
    """Thin container grouping related experiments."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    experiments: Mapped[list["Experiment"]] = relationship(back_populates="project")


class Target(ModelBase):
    """The antigen designed against (e.g. HER2 / 1N8Z). Reusable across experiments."""

    __tablename__ = "targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(500))
    pdb_id: Mapped[str | None] = mapped_column(String(16))  # "1N8Z"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    experiments: Mapped[list["Experiment"]] = relationship(back_populates="target")


class Recipe(ModelBase):
    """A Bio Discovery workflow = a composition of modules."""

    __tablename__ = "recipes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    recipe_type: Mapped[str | None] = mapped_column(String(64))  # "Hosted" / custom
    author: Mapped[str | None] = mapped_column(String(255))
    version: Mapped[str | None] = mapped_column(String(32))  # for the capture-as-run concern
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    modules: Mapped[list["Module"]] = relationship(secondary=recipe_modules, back_populates="recipes")
    experiments: Mapped[list["Experiment"]] = relationship(back_populates="recipe")


class Experiment(ModelBase):
    """One run: a recipe against a target, with config (params), producing candidates."""

    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))

    # Container FKs are nullable + ON DELETE SET NULL: deleting a project/recipe/target orphans, not deletes,
    # the experiment (it keeps its data). SQL: project_id UUID REFERENCES projects(id) ON DELETE SET NULL
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"))
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("recipes.id", ondelete="SET NULL"))
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("targets.id", ondelete="SET NULL"))

    # Recipe-variable run config (model_type, num_designs, hotspots, design_loops…). SQL: params JSONB NOT NULL DEFAULT '{}'
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    source_filename: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project | None"] = relationship(back_populates="experiments")
    recipe: Mapped["Recipe | None"] = relationship(back_populates="experiments")
    target: Mapped["Target | None"] = relationship(back_populates="experiments")
    candidates: Mapped[list["Candidate"]] = relationship(back_populates="experiment", cascade="all, delete-orphan")


class Artifact(ModelBase):
    """A non-scalar output (structure/sequence file) referenced by URI.

    Served by `Candidate.artifacts` in the API and EMPTY today: nothing writes rows yet, and the
    predicted structures live on disk (see `app/routers/demo.py`). A loader is a later job.
    """

    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))  # "structure" / "sequence"
    uri: Mapped[str] = mapped_column(String(1000))  # "s3://bucket/…" or "https://…"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    candidate: Mapped["Candidate"] = relationship(back_populates="artifacts")


class Candidate(ModelBase):
    """A designed sequence + its scores."""

    __tablename__ = "candidates"
    __table_args__ = (
        UniqueConstraint("experiment_id", "sequence_id", name="uq_candidate_seq"),
        Index("ix_candidates_scores_gin", "scores", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"))

    # `Mapped[SequenceId]` needs no `type_annotation_map`: `mapped_column()` gives the column type
    # explicitly, so the alias is only ever read as an annotation. Still VARCHAR(255), unchanged.
    sequence_id: Mapped[SequenceId] = mapped_column(String(255))  # Bio Discovery's per-candidate id
    # The raw {column_key: value} bag — every export column lands here untyped (default=dict → '{}').
    # GIN-indexed above so we can query INSIDE it, e.g. WHERE (scores->>'pseudo_perplexity')::float < 10
    scores: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    annotation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # --- relationships ---
    experiment: Mapped["Experiment"] = relationship(back_populates="candidates")
    chains: Mapped[list["CandidateChain"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")


class ChainRole(enum.Enum):
    """What part a chain plays in the assembly.
    HEAVY and LIGHT are the antibody's own two chains;
    TARGET is the antigen it was folded against
      — a role, not a class of antibody chain,
    which is why this is not ChainType.
    """

    HEAVY = "H"
    LIGHT = "L"
    TARGET = "T"


class CandidateChain(ModelBase):
    """An amino acid chain candidate."""

    __tablename__ = "candidate_chains"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[ChainRole] = mapped_column(SAEnum(ChainRole))
    sequence: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(INTEGER, default=0)  # disambiguates repeated labels (e.g. 2 target chains)

    candidate: Mapped["Candidate"] = relationship(back_populates="chains")
