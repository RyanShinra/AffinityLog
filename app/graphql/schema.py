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
from collections.abc import Sequence

import strawberry
from graphql import GraphQLError
from sqlalchemy import Result, Select

from app.graphql.context import Context
from app.graphql.errors import MaskInternalErrors
from app.graphql.types import Candidate, Experiment
from app.models import orm as db

# ---------------------------------------------------------------------------------------------
# WHY NOTHING BELOW WRAPS `session.execute` IN try/except
#
# It can certainly fail — a dropped connection, a timeout, a deadlock, a statement the database
# rejects. Every one of those raises out of the resolver, and that is intended: Strawberry catches
# it at the boundary, logs it in full, and MaskInternalErrors replaces the message with a generic
# one before the client sees it. Catching per-resolver would duplicate the same block six times,
# force each one to invent a return value, and still not cover anything raised outside the block.
#
# The one thing worth knowing is which call can raise WHAT. `.scalars().all()` never raises;
# `.scalar_one_or_none()` raises MultipleResultsFound on more than one row. Both are split onto
# their own line below so the hazard is attached to the line that owns it.
#
# See app/graphql/errors.py for the policy and the measurements behind it.
# ---------------------------------------------------------------------------------------------


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

    The `code` is also what keeps this error visible at all: `MaskInternalErrors` replaces the
    message of anything WITHOUT one, so a deliberate error that forgot to set a code would be masked
    along with the genuine faults. See app/graphql/errors.py.
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
        stmt: Select[tuple[db.Experiment]] = Experiment.select_statement().order_by(db.Experiment.name)
        result: Result[tuple[db.Experiment]] = await info.context.execute(stmt)
        rows: Sequence[db.Experiment] = result.scalars().all()

        return [Experiment.from_row(r) for r in rows]

    @strawberry.field
    async def experiment(self, info: strawberry.Info[Context, None], id: strawberry.ID) -> Experiment | None:
        search_id: uuid.UUID = _as_uuid(str(id))  # This may throw, see above

        stmt: Select[tuple[db.Experiment]] = Experiment.select_statement().where(db.Experiment.id == search_id)
        result: Result[tuple[db.Experiment]] = await info.context.execute(stmt)
        # Filtered on the primary key, so at most one row can come back. scalar_one_or_none raises
        # MultipleResultsFound above one — unreachable here, and that unreachability is the point:
        # it asserts the invariant, where .first() would quietly return an arbitrary row instead.
        row: db.Experiment | None = result.scalar_one_or_none()

        if row is None:
            return None
        return Experiment.from_row(row)

    @strawberry.field
    async def candidates(self, info: strawberry.Info[Context, None]) -> list[Candidate]:
        # The selectinload inside select_statement() is what makes this two queries rather than
        # fifteen: SQLAlchemy issues one SELECT for the candidates and a second for all their chains
        # at once, instead of a per-candidate lazy load that async would refuse to perform anyway.
        # Measured at 2 — but measured at 2 for `{ candidates { sequenceId } }` as well, where the
        # second query fetches chains nobody asked for. See types.py's "MEASURED COST".
        stmt: Select[tuple[db.Candidate]] = Candidate.select_statement().order_by(db.Candidate.sequence_id)
        result: Result[tuple[db.Candidate]] = await info.context.execute(stmt)
        rows: Sequence[db.Candidate] = result.scalars().all()

        return [Candidate.from_row(r) for r in rows]

    @strawberry.field
    async def candidate(self, info: strawberry.Info[Context, None], id: strawberry.ID) -> Candidate | None:
        search_id: uuid.UUID = _as_uuid(str(id))  # This may throw, see above

        stmt: Select[tuple[db.Candidate]] = Candidate.select_statement().where(db.Candidate.id == search_id)
        result: Result[tuple[db.Candidate]] = await info.context.execute(stmt)
        row: db.Candidate | None = result.scalar_one_or_none()  # PK filter — see `experiment` above

        if row is None:
            return None
        return Candidate.from_row(row)


# Building this object is what generates the SDL — there is no schema file to keep in sync, which is
# the code-first bargain: one definition, but the contract only becomes visible once the code runs.
# TODO: no test asserts on the printed SDL yet, so nothing currently checks this against the spec in
# docs/graphql-schema.md. `schema.as_str()` is the hook for one.
schema = strawberry.Schema(query=Query, extensions=[MaskInternalErrors])
