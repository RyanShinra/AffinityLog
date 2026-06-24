"""
Test configuration. Integration tests use testcontainers to spin up a real Postgres.
Unit tests for the CSV importer run without any database.
"""

import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app

SAMPLE_CSV = Path(__file__).parent.parent / "sample_data" / "her2_nanobody_sample.csv"


# ---------------------------------------------------------------------------
# In-memory / testcontainer DB fixtures
# ---------------------------------------------------------------------------


def _get_test_db_url() -> str:
    """
    Returns a Postgres URL for tests.
    Uses testcontainers if TESTCONTAINERS_POSTGRES is set or if a local pg is unavailable.
    Falls back to the env var TEST_DATABASE_URL if set.
    """
    import os

    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return url

    try:
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer("postgres:16-alpine")
        container.start()
        # Store so we can stop it at teardown; pytest fixture lifetime handles this via module
        _get_test_db_url._container = container  # type: ignore[attr-defined]
        sync_url = container.get_connection_url()
        return sync_url.replace("psycopg2", "asyncpg").replace("postgresql://", "postgresql+asyncpg://")
    except Exception:
        return "postgresql+asyncpg://affinitylog:affinitylog@localhost:5432/affinitylog_test"


@pytest.fixture(scope="session")
def db_url() -> str:
    return _get_test_db_url()


@pytest_asyncio.fixture(scope="session")
async def test_engine(db_url: str):
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    if hasattr(_get_test_db_url, "_container"):
        _get_test_db_url._container.stop()  # type: ignore[attr-defined]


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncSession:
    async_session = async_sessionmaker(test_engine, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(test_engine, db_session) -> AsyncClient:
    """HTTP test client with DB dependency overridden to use the test session."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def sample_csv_bytes() -> bytes:
    return SAMPLE_CSV.read_bytes()


@pytest.fixture
def sample_csv_content() -> str:
    return SAMPLE_CSV.read_text(encoding="utf-8")
