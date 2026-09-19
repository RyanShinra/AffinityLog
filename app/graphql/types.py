"""Strawberry types — the apparent representation, in code.

``docs/graphql-schema.md`` is the spec these implement. Strawberry is **code-first**: the SDL is
generated from these classes rather than parsed from a file, which is exactly why that document was
written and reviewed before any of this existed — there was no other point at which changing the
schema was cheap.

Two halves. The entity types (``Candidate``, ``Experiment``, ``Chain`` and their satellites) map rows.
The interpretive half — ``Metric``, ``Module``, ``Concept`` and ``ScoreEntry``, the reason the catalog
exists — sits below them. ``ScoreEntry`` is the one type backed by no table: see ``Candidate.scores``.

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

import logging
import math
import uuid
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

import strawberry
from graphql import GraphQLError
from sqlalchemy import Result, Select, select
from sqlalchemy.orm import selectinload
from strawberry.scalars import JSON

from app.catalog import variant_kind
from app.catalog.identifiers import ModuleName, SequenceId
from app.catalog.interface_kind import InterfaceKind
from app.catalog.keys import ScoreKey, decompose
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

# A different binding name from the six above, deliberately. Those rebind a name only ever reached
# through `db.`; `InterfaceKind` is imported by its own name and the `scores` resolver annotates
# with it, so rebinding it would replace the class with the registration's return value (an
# `EnumType | Callable[...]` union, not a type expression) and the annotation would fail to check.
InterfaceKindEnum = strawberry.enum(InterfaceKind)

# The first logger in `app/`. Named after the module, the standard-library way, so a handler can
# be pointed at `app.graphql` without catching Strawberry's own `strawberry.execution` output.
logger = logging.getLogger(__name__)


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
class Artifact:
    """A non-scalar output referenced by URI — today, a predicted structure file.

    Eleven rows in the loaded corpus, written by `scripts/seed_corpus_context.py` from the
    `<candidate id>_<tool>.pdb` files under `experiment_results/`: `uri` is the repo-relative
    path and `kind` is the TOOL that produced it (`boltz2`, `rfantibody`), so a candidate folded
    by two tools carries two artifacts.

    (Stage 4 was planned on the belief that nothing wrote this table. That was wrong — the
    seeder inserts with raw SQL, which a grep for `Artifact(` did not find — and was caught by
    running the resolvers against the real corpus. Measured 2026-09-18: 11 artifacts.)
    """

    kind: str
    uri: str

    @staticmethod
    def from_row(row: db.Artifact) -> Artifact:
        return Artifact(kind=row.kind, uri=row.uri)


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

    `scores` is a resolver over the private `score_bag`, not a field: turning the JSONB bag into
    interpreted `ScoreEntry` values needs the catalog memo AND this candidate's `interface_kind`
    from the `candidate_summary` view, which is what disambiguates three of the 200 keys.
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

    # The raw bag, kept off the schema. `scores` below is a RESOLVER over this, so the JSONB dict
    # is never exposed as-is: the whole point of ScoreEntry is that a key means nothing until the
    # catalog and the candidate's chains have both been consulted.
    score_bag: strawberry.Private[dict[str, str]]

    @staticmethod
    def from_row(row: db.Candidate) -> Candidate:
        return Candidate(
            id=strawberry.ID(str(row.id)),
            sequence_id=row.sequence_id,
            annotation=row.annotation,
            chains=[Chain.from_row(c) for c in row.chains],
            experiment_id=row.experiment_id,
            score_bag=row.scores,
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

    @strawberry.field
    async def scores(
        self,
        info: strawberry.Info[Context, None],
        module: str | None = None,
        chain: db.ChainRole | None = None,
        concept: str | None = None,
    ) -> list[ScoreEntry]:
        """The candidate's score bag, interpreted: one entry per JSONB key, plus what it means.

        Backed by no table. Each entry is one bag key, the catalog row `metric_for` picks for it,
        and the interface kind that lets `metric_for` pick. No SQL runs here: both memos load once
        per request under their own locks, so `{ candidates { scores } }` is dict lookups from
        here on. The filters run in Python for the reason docs/scoreentry-plan.md gives: pushing
        them into JSONB would mean re-deriving `decompose()` in SQL.

        Sorted by key. JSONB does not preserve insertion order, so without this the list order
        would depend on Postgres's key hashing and nothing else.
        """
        catalog: MetricCatalog = await info.context.catalog()
        interface_kind_per_candidate = await info.context.interface_kinds()
        # `.get`, not `[]`: a candidate absent from the view is possible in principle, and
        # `metric_for` already raises the coded error for the one case where that matters (an
        # INTERFACE-qualified heading). Every other heading resolves fine without it.
        interface_kind: InterfaceKind | None = interface_kind_per_candidate.get(SequenceId(self.sequence_id))

        entries: list[ScoreEntry] = []
        for key in sorted(self.score_bag):
            value: str = self.score_bag[key]
            score_key: ScoreKey = decompose(key)
            db_metric: db.Metric | None = catalog.metric_for(score_key, interface_kind)

            if not _passes_filters(score_key, db_metric, module=module, chain=chain, concept=concept):
                continue

            metric: Metric | None = None
            if db_metric is not None:
                metric = Metric.from_row(db_metric)

            entries.append(
                ScoreEntry(
                    key=key,
                    value=value,
                    numeric_value=_numeric_value(value, db_metric),
                    chain=score_key.chain,
                    metric=metric,
                )
            )
        return entries

    @strawberry.field
    async def interface_kind(self, info: strawberry.Info[Context, None]) -> InterfaceKind:
        """What this candidate's ipTM-style scores are measuring: the view's CASE, as an enum.

        From the per-request memo, so no query of its own. NON-NULL, which is why absence is an
        error rather than a null: see `_interface_kind_of`.
        """
        interface_kind_per_candidate = await info.context.interface_kinds()
        return _interface_kind_of(SequenceId(self.sequence_id), interface_kind_per_candidate)

    @strawberry.field
    async def target(self, info: strawberry.Info[Context, None]) -> Target | None:
        """The antigen this candidate was designed against, hoisted through its experiment.

        Two FK hops flattened to one field, served by ONE join. Not `Experiment.select_statement()`
        filtered by id: that carries three eager loads, which is how `candidates { experiment
        { name } }` came to cost 58 queries in the measured-cost table. A client asking for both
        `experiment` and `target` pays for the experiment twice, and that is the cheaper trade.
        """
        stmt: Select[tuple[db.Target]] = (
            select(db.Target)
            .join(db.Experiment, db.Experiment.target_id == db.Target.id)
            .where(db.Experiment.id == self.experiment_id)
        )
        result: Result[tuple[db.Target]] = await info.context.execute_statement(stmt)
        row: db.Target | None = result.scalar_one_or_none()  # experiment PK, at most one
        if row is None:
            return None
        return Target.from_row(row)

    @strawberry.field
    async def artifacts(self, info: strawberry.Info[Context, None]) -> list[Artifact]:
        """Files attached to this candidate — its predicted structures, for eleven of fourteen.

        A resolver rather than a `selectinload` in `select_statement()`, because an eager load
        would add a query to every candidate read whether or not the client asked. Sorted so the
        order is the data's, not the planner's.
        """
        stmt: Select[tuple[db.Artifact]] = (
            select(db.Artifact)
            .where(db.Artifact.candidate_id == uuid.UUID(self.id))
            .order_by(db.Artifact.kind, db.Artifact.uri)
        )
        result: Result[tuple[db.Artifact]] = await info.context.execute_statement(stmt)
        rows: Sequence[db.Artifact] = result.scalars().all()
        return [Artifact.from_row(r) for r in rows]


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


# ---------------------------------------------------------------------------
# SCORE ENTRIES — the bag, interpreted. Backed by no table.
# ---------------------------------------------------------------------------


@strawberry.type
class ScoreEntry:
    """One key of a candidate's score bag, and what the catalog says it means.

    `value` is the stored string, uncoerced, always. `numeric_value` is a separate field rather
    than a best-effort cast, so a CATEGORICAL that happens to look like a number never becomes one
    (docs/graphql-schema.md, "value is never coerced"). `chain` is the suffix `decompose()`
    stripped: it says which subject the value describes, not which metric it is, which is why it
    is here and not on `Metric`. `metric` is nullable because an unresolvable key is information,
    not an error: it says the column did not come from a catalogued module.
    """

    key: str
    value: str
    numeric_value: float | None
    chain: db.ChainRole | None
    metric: Metric | None


def _interface_kind_of(
    sequence_id: SequenceId, interface_kind_per_candidate: Mapping[SequenceId, InterfaceKind]
) -> InterfaceKind:
    """This candidate's interface kind, or a coded error. Never None.

    `Candidate.interfaceKind` is non-null, and a non-null field that raises does not stop at the
    Candidate. Every item of `candidates: [Candidate!]!` is non-null too, so the null propagates to
    the root and the WHOLE RESPONSE comes back `data: null`. This docstring said "takes the whole
    Candidate with it" until the review of PR #16, and the decision to raise was argued on that
    smaller blast radius.

    The view's shape rules the absence out: every candidate joins an experiment on a non-null key,
    and chains are LEFT joined. The one way through is isolation. The request runs at READ
    COMMITTED, so a candidate deleted by another request between the candidate select and this
    map's read is in the list and not in the map. Nothing deletes today. Whether raising still holds
    up is deferred to issue #17. Coded, so `MaskInternalErrors` lets it through — the same
    reasoning as `metric_for`'s error.

    The `scores` resolver deliberately does NOT use this: a candidate absent from the view can
    still resolve every non-INTERFACE heading, and `metric_for` raises only for the ones it cannot.
    """
    interface_kind = interface_kind_per_candidate.get(sequence_id)
    if interface_kind is None:
        raise GraphQLError(
            f"candidate {sequence_id!r} is not in the candidate_summary view, so its interface kind is "
            "unknown. The view's shape rules that out, so the likely cause is a delete committed by "
            "another request between this request's statements; retrying should succeed.",
            extensions={"code": "CANDIDATE_NOT_IN_SUMMARY"},
        )
    return interface_kind


def _numeric_value(value: str, db_metric: db.Metric | None) -> float | None:
    """`value` as a float, or None. Populated only when the catalog says the metric is numeric.

    docs/graphql-schema.md §"numericValue parses defensively": of the 995 values whose metric says
    FLOAT or INT, all 995 parse today, and that is an artifact of how `value_type` was inferred
    rather than a guarantee. "<40" and "-" are already in the corpus under CATEGORICAL rows, one
    correction away from sitting under a numeric one.

    Three choices, each deliberate:
      * FLOAT and INT only. A BOOL stored as "1" would `float()` happily, and then a truth value
        would be served as a measurement.
      * `ValueError` only, not `Exception`. `value` is typed `str` all the way down, so a
        `TypeError` here is OUR bug and should surface, not be swallowed into a null.
      * A parse failure logs at DEBUG. It is a data-quality signal (a censored value under a
        numeric metric) and not an alarm, and it fires once per bad value per request.

    And one that is not a choice: NON-FINITE IS A FAILURE TOO. `float()` accepts "nan", "inf"
    and "-Infinity", and GraphQL's Float cannot represent any of them — graphql-core's
    serializer raises, and MaskInternalErrors turns that into "Internal server error." for the
    entry. So a value that parses but cannot be served declines to null the same way a value
    that does not parse does. Found in review of PR #16; no corpus CSV contains one today.
    """
    if db_metric is None:
        return None
    if db_metric.value_type not in (db.MetricValueType.FLOAT, db.MetricValueType.INT):
        return None
    try:
        parsed = float(value)
    except ValueError:
        logger.debug(
            "score value %r under numeric metric %s.%s does not parse; numericValue is null",
            value,
            db_metric.module.name,
            db_metric.column_key,
        )
        return None
    if not math.isfinite(parsed):
        logger.debug(
            "score value %r under numeric metric %s.%s is not finite; numericValue is null",
            value,
            db_metric.module.name,
            db_metric.column_key,
        )
        return None
    return parsed


def _passes_filters(
    score_key: ScoreKey,
    db_metric: db.Metric | None,
    *,
    module: str | None,
    chain: db.ChainRole | None,
    concept: str | None,
) -> bool:
    """Whether one entry survives the `scores(module:, chain:, concept:)` arguments.

    Three independent filters. `None` means "not filtering on this", for all three: an explicit
    `chain: null` reads the same as omitting it. The three-valued version (`strawberry.UNSET` as
    the default, so `null` could mean "unsuffixed entries only") was considered and rejected:
    nothing asks for it, and a client can read `chain == null` off the response.

    `module` matches the KEY's prefix, not the catalog's module. They agree for every catalogued
    key and differ for an uncatalogued one, which has no metric to match: matching the key keeps
    it, so `module: "mystery"` finds "mystery.column" whether or not the catalog knows it. The
    client typed a string it can see in `key`; that is the thing it should match. `concept` has
    no such choice, since a concept exists only through a metric, so it drops uncatalogued keys.
    """
    if module is not None and score_key.module != module:
        return False
    if chain is not None and score_key.chain != chain:
        return False
    if concept is not None:
        if db_metric is None or db_metric.concept is None:
            return False
        if db_metric.concept.name != concept:
            return False
    return True
