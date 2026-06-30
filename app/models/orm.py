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

from sqlalchemy import (
    ARRAY,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# ---------------------------------------------------------------------------
# Enums (controlled vocabularies). Each becomes a native Postgres ENUM type.
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


class VariantKind(enum.Enum):
    PARAMETER = "parameter"
    MODE = "mode"
    SOURCE_MODEL = "source_model"
    COMPONENT = "component"


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
    Base.metadata,
    Column("recipe_id", UUID(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True),
    Column("module_id", UUID(as_uuid=True), ForeignKey("modules.id", ondelete="CASCADE"), primary_key=True),
)


# ===========================================================================
# CATALOG SIDE (seeded reference data)
# ===========================================================================


class Module(Base):
    """
    An algorithmic unit in Bio Discovery (e.g. "Boltz2", "PLM Pseudo-Perplexity").

    *** This is the fully-commented REFERENCE model — copy these patterns. ***
    """

    __tablename__ = "modules"

    # UUID primary key. `Mapped[uuid.UUID]` is the Python-side type; `mapped_column(...)` is the
    # column. default=uuid.uuid4 means Python generates the id (vs server_default for DB-side).
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Non-Optional Mapped[str] → NOT NULL. unique=True → a UNIQUE index on the column.
    name: Mapped[str] = mapped_column(String(128), unique=True)

    # Enum → a native Postgres ENUM type.  SQL: module_type moduletype NOT NULL
    module_type: Mapped[ModuleType] = mapped_column(SAEnum(ModuleType))

    # Postgres native ARRAY of enum. Later we query it with array operators, e.g.
    #   SELECT * FROM modules WHERE functions && ARRAY['stability']::modulefunction[];
    functions: Mapped[list[ModuleFunction]] = mapped_column(ARRAY(SAEnum(ModuleFunction)), default=list)

    # The `| None` is what makes it NULLABLE (like C#'s `string?`).
    repo_url: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)

    # server_default=func.now() → the DATABASE writes the timestamp (DEFAULT now()), not Python.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships — Python-side nav properties, NOT columns.
    # One Module → many Metric. back_populates wires both sides; cascade deletes its metrics with it.
    metrics: Mapped[list["Metric"]] = relationship(back_populates="module", cascade="all, delete-orphan")
    # Other side of the M2M; `secondary` points at the association table above.
    recipes: Mapped[list["Recipe"]] = relationship(secondary=recipe_modules, back_populates="modules")


class Metric(Base):
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
            "module_id", "column_key", "variant_kind", "variant",
            name="uq_metric_identity", postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    module_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("modules.id", ondelete="CASCADE"))
    column_key: Mapped[str] = mapped_column(String(128))   # raw export header, e.g. "pseudo_perplexity"
    display_name: Mapped[str] = mapped_column(String(255))  # "ESM2 Perplexity"
    value_type: Mapped[MetricValueType] = mapped_column(SAEnum(MetricValueType))
    unit: Mapped[str | None] = mapped_column(String(32))    # "kcal/mol", "°C"; NULL = dimensionless
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
    variant: Mapped[str | None] = mapped_column(String(128))
    
    concept_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("concepts.id", ondelete="SET NULL"))
    concept: Mapped["Concept | None"] = relationship(back_populates="metrics")
     
    # Benchmark normalization, e.g. {"type": "mahalanobis_gaussian", "params": {"mu": 3.59, "sigma": 7.47}}
    transform: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    module: Mapped["Module"] = relationship(back_populates="metrics")


class Concept(Base):
    __tablename__ = "concepts"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    
    metrics: Mapped[list["Metric"]] = relationship(back_populates="concept")
    
# ===========================================================================
# EXPERIMENT SIDE (ingested data)
# ===========================================================================


class Project(Base):
    """Thin container grouping related experiments."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    experiments: Mapped[list["Experiment"]] = relationship(back_populates="project")


class Target(Base):
    """The antigen designed against (e.g. HER2 / 1S78). Reusable across experiments."""

    __tablename__ = "targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(500))
    pdb_id: Mapped[str | None] = mapped_column(String(16))  # "1S78"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    experiments: Mapped[list["Experiment"]] = relationship(back_populates="target")


class Recipe(Base):
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


class Experiment(Base):
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
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_filename: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    project: Mapped["Project | None"] = relationship(back_populates="experiments")
    recipe: Mapped["Recipe | None"] = relationship(back_populates="experiments")
    target: Mapped["Target | None"] = relationship(back_populates="experiments")
    candidates: Mapped[list["Candidate"]] = relationship(back_populates="experiment", cascade="all, delete-orphan")


class Artifact(Base):
    """A non-scalar output (structure/sequence file) referenced by URI. The placeholder hook for now."""

    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))     # "structure" / "sequence"
    uri: Mapped[str] = mapped_column(String(1000))    # "s3://bucket/…" or "https://…"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    candidate: Mapped["Candidate"] = relationship(back_populates="artifacts")


# ===========================================================================
# ░░░  YOUR TURN  ░░░   Candidate — finish the TODOs below.
# The id, the experiment_id FK, and the two relationships are pre-wired so the
# file imports and the Experiment/Artifact sides resolve. Add the rest.
# ===========================================================================


class Candidate(Base):
    """A designed sequence + its scores."""

    __tablename__ = "candidates"
    __table_args__ = (UniqueConstraint("experiment_id", "sequence_id", name="uq_candidate_seq"), 
                      Index("ix_candidates_scores_gin", "scores", postgres_using="gin"))

    # --- provided (so the file imports & relationships resolve) ---
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"))

    sequence_id: Mapped[str] = mapped_column(String(255))
    fasta_sequence: Mapped[str] = mapped_column(Text)
    scores: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    annotation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    # --- TODO (you): add these columns, copying the patterns from `Module` above ---
    #   sequence_id     : str         NOT NULL   → String(255)   (Bio Discovery's per-candidate id)
    #   fasta_sequence  : str         NOT NULL   → Text          (the amino-acid sequence)
    #   scores          : dict                   → JSONB, default=dict
    #                       raw SQL:  scores JSONB NOT NULL DEFAULT '{}'   (the {column_key: value} bag)
    #   annotation      : str | None  NULLABLE   → Text
    #   created_at      : datetime               → DateTime(timezone=True), server_default=func.now()
    #
    # --- TODO (you): add __table_args__ = ( ... ) with BOTH of these ---
    #   1) UniqueConstraint("experiment_id", "sequence_id", name="uq_candidate_seq")
    #        raw SQL:  UNIQUE (experiment_id, sequence_id)     ← one sequence_id per experiment
    #   2) Index("ix_candidates_scores_gin", "scores", postgresql_using="gin")
    #        raw SQL:  CREATE INDEX ix_candidates_scores_gin ON candidates USING gin (scores);
    #        (lets us query INSIDE the JSONB, e.g.  WHERE (scores->>'pseudo_perplexity')::float < 10)
    #
    # (UniqueConstraint, Index, JSONB, Text, String, DateTime, func, mapped_column, Mapped
    #  are all already imported at the top — you don't need to touch the imports.)

    # --- relationships (provided) ---
    experiment: Mapped["Experiment"] = relationship(back_populates="candidates")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
