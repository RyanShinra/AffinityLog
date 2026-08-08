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
from graphql import GraphQLError
from sqlalchemy import select

from app.graphql.context import Context
from app.graphql.types import CANDIDATE_LOADS, EXPERIMENT_LOADS, Candidate, Experiment
from app.models import orm as db


def _as_uuid(value: str) -> uuid.UUID:
    """Parse a client-supplied id, failing loudly when it is malformed.

    Two situations that must not look alike to a caller: a well-formed id matching no row is a
    legitimate `null`, but a malformed id is the client's own bug. Collapsing both into `null` tells
    someone who sent garbage that their record simply is not there, which is the wrong answer.

    Raising is safe here, and that was measured rather than assumed: Strawberry sends `str(exception)`
    as the error message and logs the traceback server-side rather than shipping it. Because the
    fields below are nullable, the client gets *both* halves — the field comes back null AND an
    `errors` entry explains why. `GraphQLError` additionally carries machine-readable `extensions`,
    so a client can branch on the code instead of matching on prose.

    (A deployment facing untrusted callers would want an extension that masks any exception which is
    NOT a deliberate GraphQLError — an internal `KeyError` currently reaches the client as its
    message, though never as a traceback. Local demo, so not done here.)
    """
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise GraphQLError(
            f"malformed id: {value!r} is not a UUID",
            extensions={"code": "BAD_USER_INPUT"},
        ) from exc


@strawberry.type
class Query:
    """Entry points. Deliberately plain lists — no pagination, no Relay connections.

    Nine experiments and fourteen candidates. Cursors here would be ceremony rather than capability;
    the spec records that as a decision so it does not read as an oversight later.
    """

    @strawberry.field
    async def experiments(self, info: strawberry.Info[Context, None]) -> list[Experiment]:
        stmt = select(db.Experiment).options(*EXPERIMENT_LOADS).order_by(db.Experiment.name)
        rows = (await info.context.session.execute(stmt)).scalars().all()
        return [Experiment.from_row(r) for r in rows]

    @strawberry.field
    async def experiment(self, info: strawberry.Info[Context, None], id: strawberry.ID) -> Experiment | None:
        stmt = select(db.Experiment).where(db.Experiment.id == _as_uuid(str(id))).options(*EXPERIMENT_LOADS)
        row = (await info.context.session.execute(stmt)).scalar_one_or_none()
        return Experiment.from_row(row) if row is not None else None

    @strawberry.field
    async def candidates(self, info: strawberry.Info[Context, None]) -> list[Candidate]:
        # CANDIDATE_LOADS is what makes this two queries rather than fifteen: SQLAlchemy issues one
        # SELECT for the candidates and a second for all their chains at once, instead of a
        # per-candidate lazy load that async would refuse to perform anyway. Measured at 2 — but
        # measured at 2 for `{ candidates { sequenceId } }` as well, where the second query fetches
        # chains nobody asked for. See types.py's "MEASURED COST".
        stmt = select(db.Candidate).options(CANDIDATE_LOADS).order_by(db.Candidate.sequence_id)
        rows = (await info.context.session.execute(stmt)).scalars().all()
        return [Candidate.from_row(r) for r in rows]

    @strawberry.field
    async def candidate(self, info: strawberry.Info[Context, None], id: strawberry.ID) -> Candidate | None:
        stmt = select(db.Candidate).where(db.Candidate.id == _as_uuid(str(id))).options(CANDIDATE_LOADS)
        row = (await info.context.session.execute(stmt)).scalar_one_or_none()
        return Candidate.from_row(row) if row is not None else None


# Building this object is what generates the SDL — there is no schema file to keep in sync, which is
# the code-first bargain: one definition, but the contract only becomes visible once the code runs.
# TODO: no test asserts on the printed SDL yet, so nothing currently checks this against the spec in
# docs/graphql-schema.md. `schema.as_str()` is the hook for one.
schema = strawberry.Schema(query=Query)
