"""The GraphQL request context — how a resolver gets at the database.

WHAT A CONTEXT IS
-----------------
Resolvers are called by the GraphQL executor, not by the web framework, so they cannot take
FastAPI dependencies of their own. The context is the one object the framework hands the executor
at the start of a request, and it is the only channel through which per-request state (the session,
the authenticated user, the raw request) reaches a resolver. Every resolver in this app reads it as
``info.context``.

THE DECISION THIS FILE ENCODES
------------------------------
One session per HTTP request, shared by every resolver in the query tree — NOT one session per
resolver. A GraphQL query is a tree, so a single request can touch experiments, their candidates,
and those candidates' chains; giving each resolver its own session would spread one logical read
across several transactions, so a concurrent write could land between them and the response would
contain rows that never coexisted. One session is also one connection from the pool, rather than
one per node in the tree.

``app/database.py``'s ``get_session`` already provides exactly this shape, and this is the same
decision its docstring records. (``CLAUDE.md`` describes an engine-per-resolver design instead —
that note predates the session work and is stale; the code is right.)

WHY SUBCLASS ``BaseContext``
----------------------------
``strawberry.fastapi.BaseContext`` sets ``request``, ``response`` and ``background_tasks`` for you.
Nothing here needs them yet, but a plain dataclass would have to be replaced the first time
anything wants a header or a cookie, and subclassing costs nothing today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

# TEMPORARY: these three are used only by the not-yet-written body of `Context.catalog()` below, so
# ruff sees them as unused and the pre-commit hook would reject the commit. `unfixable = ["F401"]` in
# pyproject stops ruff DELETING them, which is the behaviour that matters here. Drop the noqa the
# moment `catalog()` has a body.
from sqlalchemy import Select, select  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload  # noqa: F401
from strawberry.fastapi import BaseContext

from app.database import get_session
from app.models import orm as db

# The catalog's natural key: (module_name, column_key, variant_kind, variant). Matches `metrics`'
# UNIQUE constraint and the first four fields of `ScoreKey`. Note `variant_kind` is the member NAME
# as a string ("INTERFACE", "PARAMETER") — that is what the Postgres enum stores and what
# `decompose()` produces, so both sides already speak it.
#
# This probably wants to live in `app/catalog/keys.py` beside `ScoreKey` once `ScoreKey.identity`
# exists and returns one. Here for now so this file stands alone.
MetricIdentity = tuple[str, str, str | None, str | None]


@dataclass(frozen=True)
class MetricCatalog:
    """Every catalogued metric, indexed the two ways the ScoreEntry resolver needs to ask.

    Both indexes come from ONE pass over ONE query. They answer different questions:

      * `by_identity` — "what does this exact identity mean?" The 1132 lookups a full
        `{ candidates { scores } }` performs are all dict hits against this.

      * `variant_kinds` — "is this (module, column_key) interface-qualified at all?" This is the
        first tier of the two-tier lookup: 197 of 200 keys carry their whole identity, and the
        3 that do not need the candidate's interface kind folded in before `by_identity` can be
        consulted. Asking this first is what keeps interface-qualification from reading as a
        general retry-on-miss — see docs/graphql-schema.md, "Which catalog row a key means is a
        two-tier question".
    """

    by_identity: dict[MetricIdentity, db.Metric]
    variant_kinds: dict[tuple[str, str], frozenset[str]]
    # End MetricCatalog Class


class Context(BaseContext):
    """Per-request state handed to every resolver as ``info.context``.

    Deliberately a thin holder. Anything that queries belongs in a resolver, not in here — the
    context exists to carry the session, not to become a service layer.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__()  # BaseContext populates request/response/background_tasks
        self.session: AsyncSession = session

        # `None` rather than an empty MetricCatalog: an empty catalog is a legitimate state (an
        # unseeded database), so the sentinel has to be distinguishable from the real thing or a
        # fresh install would query once per ScoreEntry forever.
        self._catalog: MetricCatalog | None = None

    async def catalog(self) -> MetricCatalog:
        """The whole metric catalog, loaded once per request.

        144 rows, and the `metrics` table is 152 KB in total — smaller than a single candidate's
        score bag. Loading it whole turns what would be 1132 round trips into 1132 dict lookups.

        Memoized on the instance, not at module level: the catalog only changes on a reseed, so a
        process-wide cache is tempting, but it buys a staleness window and an invalidation story in
        exchange for one query per request.
        """
        if self._catalog is not None:
            return self._catalog

        # YOUR TURN. Roughly eight lines: build the statement, execute it, walk the rows once
        # filling both dicts, assign to self._catalog, return it.
        #
        # Three things to decide as you write it:
        #
        # 1. `row.variant_kind` is a `db.VariantKind` member (or None) and MetricIdentity wants a
        #    string. `.name` gives "INTERFACE"; `.value` gives "interface" and would silently never
        #    match anything `decompose()` produces. Verified against the database, not remembered.
        #
        # 2. `row.module` is a relationship, so it lazy-loads — which under async SQLAlchemy is not
        #    a slow path, it is a MissingGreenlet. Same for `.concept`. `selectinload` both.
        #
        # 3. `.benchmark_results` and `.transform_of` are also relationships, and the SDL exposes
        #    both. Both are empty today (0 rows), so eager-loading them costs two queries returning
        #    nothing — but NOT eager-loading them means the field raises the moment a client selects
        #    it. Cheap correctness now, or defer until there is data to load?
        #
        # stmt: Select[tuple[db.Metric]] = select(db.Metric).options(...)

    # End Context class


async def get_context(session: Annotated[AsyncSession, Depends(get_session)]) -> Context:
    """The seam between FastAPI and Strawberry, and it is one line.

    `GraphQLRouter(schema, context_getter=...)` takes a callable, and — because `GraphQLRouter` is an
    `APIRouter` subclass — FastAPI resolves that callable's parameters with the ordinary dependency
    machinery. So the `Annotated[AsyncSession, Depends(get_session)]` parameter is filled in before
    Strawberry ever sees it. That is the whole trick: the context getter is just a
    dependency-injected function that happens to return the object resolvers will read.

    The alternative worth knowing this rejects: opening the session in the function body
    (`async with AsyncSessionLocal() as s: ...`) rather than taking it as a dependency. That runs,
    but nothing would close it — `get_session` is an async *generator* dependency, so FastAPI runs
    the teardown after the response is sent. Taking it via `Depends` is what ties the session's
    lifetime to the request's.

    Note this runs for GET /graphql too, not only for queries: the playground will not render if it
    raises.
    """
    return Context(session=session)
