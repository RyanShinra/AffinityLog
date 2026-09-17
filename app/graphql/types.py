"""Strawberry types — the apparent representation, in code.

``docs/graphql-schema.md`` is the spec these implement. Strawberry is **code-first**: the SDL is
generated from these classes rather than parsed from a file, which is exactly why that document was
written and reviewed before any of this existed — there was no other point at which changing the
schema was cheap.

This module covers the entity types only. ``ScoreEntry``, ``Metric``, ``Module`` and ``Concept`` —
the interpretive half, and the reason the catalog exists — land in a later pass.

HOW A RESOLVER IS SHAPED (and where graphql-js habits mislead)
-------------------------------------------------------------
In graphql-js a resolver is ``(parent, args, context, info)`` — four positional arguments. Strawberry
delivers the same four things through four *different* Python mechanisms:

    parent   -> ``self``            the resolver is a method on the type being resolved
    args     -> ordinary parameters ``async def candidate(self, id: strawberry.ID)``
    context  -> ``info.context``    our ``Context``, carrying the session
    info     -> ``info``            declared explicitly, and typed: ``strawberry.Info[Context, None]``

So there is no single signature to memorize; there is a method whose parameters happen to become
GraphQL arguments, plus an optional ``info``. Anything annotated in the parameter list becomes part
of the public schema — which is why ``info`` is special-cased by Strawberry and never appears in the
SDL, despite sitting right there in the parameter list.

TWO KINDS OF RESOLVER, AND WHY BOTH APPEAR BELOW
------------------------------------------------
A *Query* resolver (see ``schema.py``) is an entry point — it answers "find me some objects" and
starts from nothing. A *type* resolver is a field on an object that already exists, and answers
"given this object, what is its X". Mechanically they are identical; only the attachment point
differs. The tree walk that produces a composite response is nothing more than the executor calling
type resolvers on whatever the Query resolver returned, depth-first, one field at a time — and
skipping every field the client did not ask for.

That per-field call is also where N+1 comes from, and both strategies are deliberately shown here:

  * ``Candidate.chains`` is **eager** — the caller loads it with ``selectinload`` and this type
    merely holds the result. Two queries however many candidates come back.
  * ``Candidate.experiment`` is a **resolver** — a query per candidate that asks for it, and none
    at all for the candidates that do not.

MEASURED COST (2026-08-02, against the loaded corpus — 9 experiments, 14 candidates)
------------------------------------------------------------------------------------
Counted by tapping SQLAlchemy's ``before_cursor_execute``, not estimated:

    2 SELECTs   { candidates { sequenceId } }
    2 SELECTs   { candidates { sequenceId chains { chain } } }
   58 SELECTs   { candidates { experiment { name } } }
    4 SELECTs   { experiments { name } }
   22 SELECTs   { experiments { candidates { sequenceId } } }

Two things in that table are worth more than the headline N+1, because both contradict the
comfortable version of the story:

1. **Eager loading costs a query even when the field is never requested.** Asking only for
   ``sequenceId`` still costs 2, and ``experiments { name }`` costs 4 — one for the experiments and
   three for project/recipe/target that nobody asked for. ``selectinload`` is decided when the
   statement is built, and at that moment nothing has consulted the client's selection set.

2. **The N+1 multiplies by the nested eager loads.** ``candidates { experiment { name } }`` is not
   15 queries, it is 58: two for the candidates and their chains, then 14 experiments × 4 (itself
   plus its three eager loads). An N+1 sitting on top of an unconditional eager load is an N×4+1.

Both are fixable the same way — build the loader options from ``info.selected_fields`` so the
statement only fetches what was actually asked for — and neither is fixed here. At 14 rows this is
a demo running against a local Postgres, and query planning driven by the selection set is real
machinery that should be added deliberately, not smuggled in during the plumbing pass. The numbers
are recorded so the decision is made against measurements rather than instinct.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

import strawberry
from sqlalchemy import Result, Select, select
from sqlalchemy.orm import selectinload
from strawberry.scalars import JSON

from app.catalog import variant_kind
from app.catalog.identifiers import ModuleName

# from app.catalog.identifiers import ModuleName
from app.graphql.context import MetricCatalog
from app.models import orm as db

if TYPE_CHECKING:

    from app.graphql.context import Context

# Wrap the ORM's ChainRole enum rather than declaring a parallel one. `strawberry.enum` registers the
# existing class with the schema and returns it unchanged, so `db.ChainRole` stays the single chain
# vocabulary — the same reasoning that keeps InterfaceKind in one place. The GraphQL enum exposes
# the member NAMES (HEAVY/LIGHT/TARGET), not the values ("H"/"L"/"T"): that is the GraphQL
# convention, and happens to be the more readable half of the pair.
# > These bind nothing anyone uses: `strawberry.enum` REGISTERS the class with the schema and returns
# it unchanged, so the annotation to write is `db.ModuleType`, not `ModuleType` — the binding's
# inferred type is `EnumType | Callable[...]`, which is a value, not a type expression. See
# `Chain.role` for the shape.

ChainRole = strawberry.enum(db.ChainRole)
MetricValueType = strawberry.enum(db.MetricValueType)
Direction = strawberry.enum(db.Direction)
ModuleType = strawberry.enum(db.ModuleType)
VariantKind = strawberry.enum(variant_kind.VariantKind)
ModuleFunction = strawberry.enum(db.ModuleFunction)


@strawberry.type
class Project:
    name: str
    notes: str | None

    @staticmethod
    def from_row(row: db.Project) -> Project:
        return Project(name=row.name, notes=row.notes)


@strawberry.type
class Target:
    """The antigen designed against — HER2 / 1N8Z for every experiment in this corpus."""

    name: str
    pdb_id: str | None

    @staticmethod
    def from_row(row: db.Target) -> Target:
        return Target(name=row.name, pdb_id=row.pdb_id)


@strawberry.type
class Recipe:
    """A Bio Discovery workflow: a composition of modules.

    `modules` is deferred to the catalog pass, and the DAG edges *between* those modules are not
    stored at all — see `docs/recipe-topology-note.md`.
    """

    name: str
    recipe_type: str | None
    author: str | None
    version: str | None

    @staticmethod
    def from_row(row: db.Recipe) -> Recipe:
        return Recipe(name=row.name, recipe_type=row.recipe_type, author=row.author, version=row.version)


@strawberry.type
class Chain:
    """One chain of a candidate.

    A candidate has 0..N of these, which is the point: nothing in the model assumes exactly one
    antibody and one target, because nanobodies, IgGs and protein-protein docks all differ.
    `ordinal` disambiguates a repeated label — two TARGET chains, say.
    """

    role: db.ChainRole
    sequence: str
    ordinal: int

    @staticmethod
    def from_row(row: db.CandidateChain) -> Chain:
        return Chain(role=row.role, sequence=row.sequence, ordinal=row.ordinal)


@strawberry.type
class Candidate:
    """A designed sequence and its chains.

    `scores` is absent from this pass on purpose: turning the JSONB bag into interpreted
    `ScoreEntry` values is the ScoreEntry chapter, and it needs `interface_kind` from the
    `candidate_summary` view to disambiguate three of the 200 keys.
    """

    id: strawberry.ID
    sequence_id: str
    annotation: str | None

    # A plain field, not a resolver, so it costs nothing extra per candidate — but it does mean
    # `from_row` raises MissingGreenlet if the row did not come from `select_statement()`, because async
    # SQLAlchemy refuses to lazy-load from inside a running event loop. That failure is loud, which
    # is the reason to prefer it to a silent extra query per candidate.
    chains: list[Chain]

    # `strawberry.Private` keeps a field off the schema entirely: it exists on the Python object,
    # never appears in the SDL, and cannot be queried. The FK is needed by the resolver below, but
    # exposing raw foreign keys would leak the storage layout into the apparent representation —
    # the one thing this schema exists not to do.
    experiment_id: strawberry.Private[uuid.UUID]

    @staticmethod
    def from_row(row: db.Candidate) -> Candidate:
        return Candidate(
            id=strawberry.ID(str(row.id)),
            sequence_id=row.sequence_id,
            annotation=row.annotation,
            chains=[Chain.from_row(c) for c in row.chains],
            experiment_id=row.experiment_id,
        )

    @staticmethod
    def select_statement() -> Select[tuple[db.Candidate]]:
        """Use when selecting candidate rows from the database."""
        return select(db.Candidate).options(selectinload(db.Candidate.chains))

    @strawberry.field
    async def experiment(self, info: strawberry.Info[Context, None]) -> Experiment | None:
        """The run that produced this candidate.

        A type resolver: `self` is the Candidate the executor is currently walking, and the session
        arrives through `info.context` because resolvers are called by the GraphQL executor rather
        than by FastAPI, and so cannot take dependencies of their own.

        This fires once per candidate that asks for it — N+1, and deliberate at this size. But note
        what it measured at rather than what it looks like: `candidates { experiment { name } }`
        costs **58** SELECTs, not 15, because each of the 14 experiment fetches also runs the three
        eager loads inside `Experiment.select_statement()`. The nested eager load is the larger half
        of that number.

        This is where a `strawberry.dataloader.DataLoader` goes — batching the ids into one
        `WHERE id = ANY(...)` would take the 14 down to 1, though it would not touch the ×4.
        """
        stmt: Select[tuple[db.Experiment]] = Experiment.select_statement().where(db.Experiment.id == self.experiment_id)
        result: Result[tuple[db.Experiment]] = await info.context.execute_statement(stmt)
        # Filtered on the primary key, so at most one row. See the note in schema.py on why nothing
        # here is wrapped in try/except, and why scalar_one_or_none is preferred to .first().
        row: db.Experiment | None = result.scalar_one_or_none()
        if row is None:
            return None
        return Experiment.from_row(row)


@strawberry.type
class Experiment:
    """One run: a recipe against a target, with config, producing candidates."""

    id: strawberry.ID
    name: str
    # An opaque JSON scalar, and honestly so: De Novo Design and Directed Evolution have entirely
    # disjoint parameter sets, so this is a bag rather than columns. Unlike `scores`, there is no
    # param catalog to interpret it against — see the spec's "Deliberately absent" section.
    params: JSON
    source_filename: str | None
    notes: str | None

    project: Project | None
    recipe: Recipe | None
    target: Target | None

    @staticmethod
    def from_row(row: db.Experiment) -> Experiment:
        """Map an ORM row whose project/recipe/target are already loaded — i.e. one that came from
        `Experiment.select_statement()`."""
        return Experiment(
            id=strawberry.ID(str(row.id)),
            name=row.name,
            # strawberry.scalars.JSON is a NewType over object, not an alias for dict — so the
            # JSONB payload needs an explicit wrap. Nothing happens at runtime; it is the point at
            # which "arbitrary bag from the database" is declared to be the opaque scalar the
            # schema promises, and mypy strict is what makes that declaration mandatory.
            params=JSON(row.params),
            source_filename=row.source_filename,
            notes=row.notes,
            project=Project.from_row(row.project) if row.project is not None else None,
            recipe=Recipe.from_row(row.recipe) if row.recipe is not None else None,
            target=Target.from_row(row.target) if row.target is not None else None,
        )

    @staticmethod
    def select_statement() -> Select[tuple[db.Experiment]]:
        """Use when selecting experiment rows from the database."""
        return select(db.Experiment).options(
            selectinload(db.Experiment.project), selectinload(db.Experiment.recipe), selectinload(db.Experiment.target)
        )

    @strawberry.field
    async def candidates(self, info: strawberry.Info[Context, None]) -> list[Candidate]:
        """The candidates this run produced — the other direction of the same N+1 trade-off.

        Starting from `Candidate.select_statement()` is not a style choice: without its selectinload,
        `Candidate.from_row` reaching for `row.chains` would try to lazy-load inside the event loop
        and raise. Every path that builds a Candidate has to satisfy that contract, which is why the
        statement is only ever constructed there.
        """
        stmt: Select[tuple[db.Candidate]] = (
            Candidate.select_statement()
            .where(db.Candidate.experiment_id == uuid.UUID(self.id))
            .order_by(db.Candidate.sequence_id)
        )
        result: Result[tuple[db.Candidate]] = await info.context.execute_statement(stmt)
        rows: Sequence[db.Candidate] = result.scalars().all()
        return [Candidate.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# THE CATALOG — what a score MEANS.
# Every object below is already in memory once Context.catalog() has run:
# _load_catalog eager-loads module, concept, benchmark_results and transform_of.
# These are declarations over data we already fetch, not new queries.
# ---------------------------------------------------------------------------


@strawberry.type
class Concept:
    """What a metric measures, independent of which module measured it."""

    name: str
    label: str
    description: str | None

    @staticmethod
    def from_row(row: db.Concept) -> Concept:
        return Concept(name=row.name, label=row.label, description=row.description)


@strawberry.type
class BenchmarkResult:
    """One published benchmark row for a metric.

    ALWAYS EMPTY TODAY: `benchmark_results` has zero rows and the tables were deliberately never
    populated (CLAUDE.md). Built because the field is in the committed spec and `[]` is truthful —
    but nothing exercises the mapping below, so do not read a green test as coverage.
    """

    property: str
    n: int
    spearman_correlation: float
    auroc: float | None
    auprc: float | None
    precision_top5: float | None

    @staticmethod
    def from_row(row: db.BenchmarkResult) -> BenchmarkResult:
        return BenchmarkResult(
            property=row.property,
            n=row.n,
            spearman_correlation=row.spearman_correlation,
            auroc=row.auroc,
            auprc=row.auprc,
            precision_top5=row.precision_top5,
        )


@strawberry.type
class Module:
    """An algorithmic unit in Bio Discovery — Boltz2, EvoProtGrad, TemStaPro."""

    name: str  # narrowed from ModuleName: a NewType cannot cross into the SDL
    module_type: db.ModuleType
    functions: list[db.ModuleFunction]
    repo_url: str | None
    description: str | None
    version: str | None
    license: str | None

    @staticmethod
    def from_row(row: db.Module) -> Module:
        return Module(
            name=row.name,
            module_type=row.module_type,
            functions=list(row.functions),
            repo_url=row.repo_url,
            description=row.description,
            version=row.version,
            license=row.license,
        )

    @strawberry.field
    async def metrics(self, info: strawberry.Info[Context, None]) -> list[Metric]:
        """Every metric this module emits.

        A resolver rather than a plain field, and that is structural: `Metric.from_row` builds its
        `Module`, so a `Module.from_row` that built its metrics would recurse without end. A resolver
        runs only when the client asks for the field.

        Reads `metrics_per_module`, grouped once when the catalog is built — not a scan per module
        per request. No query of its own either way.
        """
        catalog: MetricCatalog = await info.context.catalog()
        rows = catalog.metrics_per_module.get(ModuleName(self.name), ())

        result: list[Metric] = []
        for row in rows:
            result.append(Metric.from_row(row))
        return result


@strawberry.type
class Metric:
    """One catalogued column, and what it means.

    NO `transformOf` YET, deliberately. `metrics.transform_of_metric_id` is a self-FK for the
    raw-vs-transformed metric pairs, and nothing in the repo writes it — not `seed/catalog.json`,
    not any seeder. Exposing it would also need care: `selectinload(transform_of)` loads exactly one
    level, so recursing `from_row` into the parent touches ITS unloaded `module` and raises
    MissingGreenlet. When something populates the column, the shape is a resolver over a by-id
    catalog index, not a field. `docs/graphql-schema.md` still specifies it.

    `benchmarkResults` below is NOT the same case and stays: it is a list, so `[]` is a truthful
    answer rather than a stand-in, and it needs no recursion.
    """

    column_key: str
    display_name: str
    value_type: db.MetricValueType
    unit: str | None
    direction: db.Direction
    variant_kind: variant_kind.VariantKind | None
    variant: str | None
    notes: str | None
    property_categories: list[str]
    module: Module
    concept: Concept | None
    benchmark_results: list[BenchmarkResult]

    @strawberry.field
    def curated(self) -> bool:
        """No column: a metric is curated when someone gave it a name other than its raw header."""
        return self.display_name != self.column_key

    @staticmethod
    def from_row(row: db.Metric) -> Metric:
        return Metric(
            column_key=row.column_key,
            display_name=row.display_name,
            value_type=row.value_type,
            unit=row.unit,
            direction=row.direction,
            variant_kind=row.variant_kind,
            variant=row.variant,
            notes=row.notes,
            property_categories=list(row.property_categories),
            module=Module.from_row(row.module),
            concept=Concept.from_row(row.concept) if row.concept is not None else None,
            benchmark_results=[BenchmarkResult.from_row(b) for b in row.benchmark_results],
        )
