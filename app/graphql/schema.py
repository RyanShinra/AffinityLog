"""The Query root and the assembled schema.

Everything here is an *entry point* — a place a query can start from. Contrast with the type
resolvers in ``types.py``, which can only answer questions about an object that already exists.
The mechanism is the same; ``Query`` is simply the type the executor begins at.

Read a resolver's `id` parameter as the thing that makes this code-first: it is an ordinary Python
parameter with an ordinary annotation, and Strawberry turns it into a GraphQL argument with a
GraphQL type. Nothing is declared twice.

WRITES ARE NOT HERE
-------------------
There is no Mutation yet. `annotateCandidate` is the only one the spec calls for, and CSV import
stays on REST — Strawberry has no native multipart support without tooling this project chose not
to add for a single endpoint. See `docs/graphql-schema.md`, "Deliberately absent".
"""

from __future__ import annotations

import uuid

import strawberry
from sqlalchemy import select

from app.graphql.context import Context
from app.graphql.types import CANDIDATE_LOADS, EXPERIMENT_LOADS, Candidate, Experiment
from app.models import orm


def _as_uuid(value: str) -> uuid.UUID | None:
    """Parse a client-supplied id, treating a malformed one as 'no such object'.

    The id arrives from outside, so a non-UUID string is input to validate rather than a bug to
    raise on: `candidate(id: "haha")` should answer null, the same as a well-formed id that matches
    nothing. Letting ValueError escape would turn a bad argument into a 500 and put a Python
    traceback in the response's `errors`.
    """
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


@strawberry.type
class Query:
    """Entry points. Deliberately plain lists — no pagination, no Relay connections.

    Nine experiments and fourteen candidates. Cursors here would be ceremony rather than capability;
    the spec records that as a decision so it does not read as an oversight later.
    """

    @strawberry.field
    async def experiments(self, info: strawberry.Info[Context, None]) -> list[Experiment]:
        stmt = select(orm.Experiment).options(*EXPERIMENT_LOADS).order_by(orm.Experiment.name)
        rows = (await info.context.session.execute(stmt)).scalars().all()
        return [Experiment.from_orm(r) for r in rows]

    @strawberry.field
    async def experiment(self, info: strawberry.Info[Context, None], id: strawberry.ID) -> Experiment | None:
        parsed = _as_uuid(str(id))
        if parsed is None:
            return None
        stmt = select(orm.Experiment).where(orm.Experiment.id == parsed).options(*EXPERIMENT_LOADS)
        row = (await info.context.session.execute(stmt)).scalar_one_or_none()
        return Experiment.from_orm(row) if row is not None else None

    @strawberry.field
    async def candidates(self, info: strawberry.Info[Context, None]) -> list[Candidate]:
        # CANDIDATE_LOADS is what makes this two queries rather than fifteen: SQLAlchemy issues one
        # SELECT for the candidates and a second for all their chains at once, instead of a
        # per-candidate lazy load that async would refuse to perform anyway. Measured at 2 — but
        # measured at 2 for `{ candidates { sequenceId } }` as well, where the second query fetches
        # chains nobody asked for. See types.py's "MEASURED COST".
        stmt = select(orm.Candidate).options(CANDIDATE_LOADS).order_by(orm.Candidate.sequence_id)
        rows = (await info.context.session.execute(stmt)).scalars().all()
        return [Candidate.from_orm(r) for r in rows]

    @strawberry.field
    async def candidate(self, info: strawberry.Info[Context, None], id: strawberry.ID) -> Candidate | None:
        parsed = _as_uuid(str(id))
        if parsed is None:
            return None
        stmt = select(orm.Candidate).where(orm.Candidate.id == parsed).options(CANDIDATE_LOADS)
        row = (await info.context.session.execute(stmt)).scalar_one_or_none()
        return Candidate.from_orm(row) if row is not None else None


# Building this object is what generates the SDL — there is no schema file to keep in sync, which is
# the code-first bargain: one definition, but the contract only becomes visible once the code runs.
# `tests/test_graphql_schema.py` prints it and asserts on the shape, so the spec in
# docs/graphql-schema.md has something to be checked against.
schema = strawberry.Schema(query=Query)
