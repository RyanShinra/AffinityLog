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
from typing import TYPE_CHECKING

import strawberry
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from strawberry.scalars import JSON

from app.models import orm

if TYPE_CHECKING:
    from app.graphql.context import Context

# What a Candidate needs loaded before `Candidate.from_orm` can touch it, and what an Experiment
# needs before `Experiment.from_orm` can. Defined once, at module level, because there are several
# call sites and the failure mode for getting it wrong is a MissingGreenlet at request time rather
# than anything a type checker would catch. Both depend only on `orm`, so they can sit up here.
#
# These are unconditional, which is their cost: EXPERIMENT_LOADS turns every experiment fetch into
# four queries whether or not the client asked for a project, recipe or target. See the "MEASURED
# COST" section of the module docstring — making them depend on `info.selected_fields` is the fix,
# and it is a deliberate piece of work rather than a tweak.
CANDIDATE_LOADS = selectinload(orm.Candidate.chains)
EXPERIMENT_LOADS = (
    selectinload(orm.Experiment.project),
    selectinload(orm.Experiment.recipe),
    selectinload(orm.Experiment.target),
)

# Wrap the ORM's Chain enum rather than declaring a parallel one. `strawberry.enum` registers the
# existing class with the schema and returns it unchanged, so `orm.Chain` stays the single chain
# vocabulary — the same reasoning that keeps InterfaceKind in one place. The GraphQL enum exposes
# the member NAMES (HEAVY/LIGHT/TARGET), not the values ("H"/"L"/"T"): that is the GraphQL
# convention, and happens to be the more readable half of the pair.
Chain = strawberry.enum(orm.Chain)


@strawberry.type
class Project:
    name: str
    notes: str | None

    @classmethod
    def from_orm(cls, row: orm.Project) -> Project:
        return cls(name=row.name, notes=row.notes)


@strawberry.type
class Target:
    """The antigen designed against — HER2 / 1N8Z for every experiment in this corpus."""

    name: str
    pdb_id: str | None

    @classmethod
    def from_orm(cls, row: orm.Target) -> Target:
        return cls(name=row.name, pdb_id=row.pdb_id)


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

    @classmethod
    def from_orm(cls, row: orm.Recipe) -> Recipe:
        return cls(name=row.name, recipe_type=row.recipe_type, author=row.author, version=row.version)


@strawberry.type
class ChainSequence:
    """One chain of a candidate.

    A candidate has 0..N of these, which is the point: nothing in the model assumes exactly one
    antibody and one target, because nanobodies, IgGs and protein-protein docks all differ.
    `ordinal` disambiguates a repeated label — two TARGET chains, say.
    """

    chain: orm.Chain
    sequence: str
    ordinal: int

    @classmethod
    def from_orm(cls, row: orm.CandidateChain) -> ChainSequence:
        return cls(chain=row.chain, sequence=row.sequence, ordinal=row.ordinal)


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
    # `from_orm` raises MissingGreenlet if the caller forgot CANDIDATE_LOADS, because async
    # SQLAlchemy refuses to lazy-load from inside a running event loop. That failure is loud, which
    # is the reason to prefer it to a silent extra query per candidate.
    chains: list[ChainSequence]

    # `strawberry.Private` keeps a field off the schema entirely: it exists on the Python object,
    # never appears in the SDL, and cannot be queried. The FK is needed by the resolver below, but
    # exposing raw foreign keys would leak the storage layout into the apparent representation —
    # the one thing this schema exists not to do.
    experiment_id: strawberry.Private[uuid.UUID]

    @classmethod
    def from_orm(cls, row: orm.Candidate) -> Candidate:
        return cls(
            id=strawberry.ID(str(row.id)),
            sequence_id=row.sequence_id,
            annotation=row.annotation,
            chains=[ChainSequence.from_orm(c) for c in row.chains],
            experiment_id=row.experiment_id,
        )

    @strawberry.field
    async def experiment(self, info: strawberry.Info[Context, None]) -> Experiment | None:
        """The run that produced this candidate.

        A type resolver: `self` is the Candidate the executor is currently walking, and the session
        arrives through `info.context` because resolvers are called by the GraphQL executor rather
        than by FastAPI, and so cannot take dependencies of their own.

        This fires once per candidate that asks for it — N+1, and deliberate at this size. But note
        what it measured at rather than what it looks like: `candidates { experiment { name } }`
        costs **58** SELECTs, not 15, because each of the 14 experiment fetches also runs the three
        EXPERIMENT_LOADS below it. The nested eager load is the larger half of that number.

        This is where a `strawberry.dataloader.DataLoader` goes — batching the ids into one
        `WHERE id = ANY(...)` would take the 14 down to 1, though it would not touch the ×4.
        """
        stmt = select(orm.Experiment).where(orm.Experiment.id == self.experiment_id).options(*EXPERIMENT_LOADS)
        row = (await info.context.session.execute(stmt)).scalar_one_or_none()
        return Experiment.from_orm(row) if row is not None else None


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

    @classmethod
    def from_orm(cls, row: orm.Experiment) -> Experiment:
        """Map an ORM row whose project/recipe/target are already loaded (EXPERIMENT_LOADS)."""
        return cls(
            id=strawberry.ID(str(row.id)),
            name=row.name,
            # strawberry.scalars.JSON is a NewType over object, not an alias for dict — so the
            # JSONB payload needs an explicit wrap. Nothing happens at runtime; it is the point at
            # which "arbitrary bag from the database" is declared to be the opaque scalar the
            # schema promises, and mypy strict is what makes that declaration mandatory.
            params=JSON(row.params),
            source_filename=row.source_filename,
            notes=row.notes,
            project=Project.from_orm(row.project) if row.project is not None else None,
            recipe=Recipe.from_orm(row.recipe) if row.recipe is not None else None,
            target=Target.from_orm(row.target) if row.target is not None else None,
        )

    @strawberry.field
    async def candidates(self, info: strawberry.Info[Context, None]) -> list[Candidate]:
        """The candidates this run produced — the other direction of the same N+1 trade-off.

        Note the CANDIDATE_LOADS: without it, `Candidate.from_orm` reaching for `row.chains` would
        try to lazy-load inside the event loop and raise. Every path that builds a Candidate has to
        satisfy that contract.
        """
        stmt = (
            select(orm.Candidate)
            .where(orm.Candidate.experiment_id == uuid.UUID(self.id))
            .options(CANDIDATE_LOADS)
            .order_by(orm.Candidate.sequence_id)
        )
        rows = (await info.context.session.execute(stmt)).scalars().all()
        return [Candidate.from_orm(r) for r in rows]
