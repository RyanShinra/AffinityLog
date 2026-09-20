import asyncio
from collections.abc import AsyncGenerator
from typing import Any, TypeVar

from sqlalchemy import Result, Select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio.engine import AsyncEngine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# One async engine per process. SQLAlchemy keeps an asyncpg connection pool underneath it;
# total DB connections ≈ (worker processes) × (pool size). `pool_pre_ping` quietly re-checks a
# connection before handing it out, so a Postgres restart doesn't surface as a random error.
engine: AsyncEngine = create_async_engine(settings.database_url, echo=settings.debug, pool_pre_ping=True)

# expire_on_commit=False keeps attributes readable after commit() — we hand ORM objects back to
# the GraphQL/REST layer past the flush, and don't want a surprise lazy-load there.
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)


class ModelBase(DeclarativeBase):
    """Declarative base for all ORM models. (SQLAlchemy docs conventionally name this `Base`;
    renamed here because `Base` collides with chemistry — acid/base and nucleobase — in this domain.)
    """


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Session-per-request unit of work — the SQLAlchemy equivalent of a *scoped* `DbContext`
    in EF Core. FastAPI/Strawberry resolve this once per HTTP request; the same session
    (one transaction boundary) is shared down the whole request and closed at the end.
    This replaces v0's session-per-resolver, which could span an experiment and its
    candidates across several sessions (inconsistent reads + N+1).
    """
    async with AsyncSessionLocal() as session:
        yield session


# `Select[tuple[X]]` -> `Result[tuple[X]]`, matching SQLAlchemy's own `Select(Generic[_TP])`.
# Named for the row tuple so the wrapper below is as precise as the `session.execute` it replaces.
# Public rather than `_RowTuple` because `app/graphql/context.py` re-uses it in its own signature.
RowTuple = TypeVar("RowTuple", bound=tuple[Any, ...])


class TaskSafeSession:
    """One `AsyncSession`, serialized so a tree of concurrent tasks can safely share it.

    WHY THIS EXISTS
    ---------------
    SQLAlchemy's own docstring on `AsyncSession` is unambiguous: it is "**not safe for use in
    concurrent tasks**". That is a real constraint here, because GraphQL resolvers are executed by
    graphql-core, which `gather`s sibling fields and list items — so several resolvers reach the
    one request-scoped session in the same tick. Measured against a real database:

        virgin session, 4 concurrent execute()  ->  3 raise InvalidRequestError
                                                    "this session is provisioning a new connection;
                                                     concurrent operations are not permitted"
        warm session,   4 concurrent execute()  ->  0 raise

    Note which one is worse. The cold failure is loud; the warm one is silent, because SQLAlchemy's
    protection sits a layer BELOW the session — `_execute_mutex` on the asyncpg connection adapter
    guards the wire protocol, not the identity map, the autoflush, or the transaction state machine.
    A warm session gets its statements serialized on the wire and its ORM state trampled anyway.

    WHY THE LOCK LIVES IN HERE AND NOT IN THE CALLER
    -----------------------------------------------
    It used to live on the GraphQL `Context`, next to a comment asking callers not to hold it across
    a call. That is the arrangement this class exists to delete. When one lock is reachable by
    everything, some caller eventually holds it across code that needs the same lock for an
    unrelated reason — and `asyncio.Lock` is not reentrant, so the task waits on itself: no
    exception, no traceback, no timeout, just one request that never answers while the server looks
    perfectly healthy.

    Giving the lock to the object that owns the session fixes that by construction rather than by
    convention. Nothing outside these few lines can acquire it, so nothing outside them can hold it
    wrongly. The critical section below is exactly one statement long and calls nothing.

    (Callers that need a longer critical section of their own — a memo, a read-modify-write — take
    their OWN lock and call `execute()` from inside it. That is safe precisely because this lock is
    a different object. See `Context.catalog()`.)

    NOT A PROXY
    -----------
    Deliberately not `__getattr__`-forwarding to the wrapped session. Every method worth exposing
    has to be added here on purpose, holding the lock, which is the point — a forwarding proxy would
    re-expose every unguarded method on `AsyncSession` and put us back where we started. Mutations
    will want `add`, `flush` and `commit`; each gets its own guarded method when it is needed.
    """

    def __init__(self, session: AsyncSession) -> None:
        # `_session` is private for the reason C++ would use a deleted copy constructor: an
        # `AsyncSession` that can be passed around is an `AsyncSession` that can be used unguarded.
        # Python cannot enforce that, but it can make reaching for it look as wrong as it is.
        self._session = session

        # Constructed eagerly rather than lazily: since 3.10 `asyncio.Lock` no longer binds to a
        # loop at construction, so there is no reason to defer it and every reason not to race on
        # creating it.
        self._lock = asyncio.Lock()

    async def execute(self, statement: Select[RowTuple]) -> Result[RowTuple]:
        """Run one statement, serialized against every other statement on this session.

        Named `execute` to mirror `AsyncSession.execute` exactly — same verb, same signature, same
        meaning, minus the concurrency hazard — so the substitution is obvious to a reader.
        """
        async with self._lock:
            # Annotated rather than returned inline: `AsyncSession.execute` is declared
            # `-> Result[Any]`, so mypy strict rejects handing that straight back as
            # `Result[RowTuple]`.
            result: Result[RowTuple] = await self._session.execute(statement)
            return result

    async def commit(self) -> None:
        """Commit the session's transaction, serialized like every statement on it.

        The second method this class was always going to grow (see NOT A PROXY above). The flush a
        commit triggers walks the identity map and writes pending state, which is precisely the
        state the lock exists to protect — so it is taken here, and nowhere a caller could hold it
        across something else. `flush` and `add` are still absent: nothing needs them yet.
        """
        async with self._lock:
            await self._session.commit()
