from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# One async engine per process. SQLAlchemy keeps an asyncpg connection pool underneath it;
# total DB connections ≈ (worker processes) × (pool size). `pool_pre_ping` quietly re-checks a
# connection before handing it out, so a Postgres restart doesn't surface as a random error.
engine = create_async_engine(settings.database_url, echo=settings.debug, pool_pre_ping=True)

# expire_on_commit=False keeps attributes readable after commit() — we hand ORM objects back to
# the GraphQL/REST layer past the flush, and don't want a surprise lazy-load there.
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


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
