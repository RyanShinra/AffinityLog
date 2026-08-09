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

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from strawberry.fastapi import BaseContext

from app.database import get_session


class Context(BaseContext):
    """Per-request state handed to every resolver as ``info.context``.

    Deliberately a thin holder. Anything that queries belongs in a resolver, not in here — the
    context exists to carry the session, not to become a service layer.
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__()  # BaseContext populates request/response/background_tasks
        self.session = session


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
