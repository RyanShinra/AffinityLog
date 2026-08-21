"""Database fixtures: a throwaway Postgres, and a session that cannot reach real data.

WHY A CONTAINER RATHER THAN THE DEV DATABASE
--------------------------------------------
Every test runs against a Postgres that testcontainers starts and destroys — `postgres:16-alpine`,
the same image `docker-compose.yml` pins, so what CI runs and what you run are the same server. It
is a *different container* from the compose one: different port, its own storage, empty until
migrated. The corpus in your compose `db` is never involved.

There is deliberately no `TEST_DATABASE_URL` escape hatch. The obvious thing to point it at is the
dev database, and these fixtures assert absolute counts against an empty schema, so that would fail
confusingly while aiming `alembic upgrade head` at real data. See CLAUDE.md.

WHY ALEMBIC AND NOT `metadata.create_all()`
-------------------------------------------
`candidate_summary` is a VIEW created by migration 004, and `app/models/views.py` is deliberately
kept out of the metadata so autogenerate does not emit CREATE TABLE for it. `create_all()` would
therefore build every table and no view — and the two-tier lookup reads `interface_kind` from that
view. Migrations are the only path that produces a complete schema.

THE THREE THINGS THAT MAKE THIS WORK
------------------------------------
1. `database_url` MUST stay synchronous. `migrations/env.py` ends in `asyncio.run(...)`, which
   raises if a loop is already running — so making this an async fixture breaks the whole suite.

2. `AsyncSessionLocal` is re-bound (the "quarantine"). `app/database.py` builds it at import time
   from `.env`, which points at the dev database. Without the re-bind, any test reaching for the
   app's own sessionmaker instead of the `session` fixture would write to the corpus. `.configure()`
   rather than assignment because the seeders do `from app.database import AsyncSessionLocal` and
   hold the object directly — rebinding the module attribute would never reach them.

3. `session` joins an outer transaction that is always rolled back. Nothing a test writes survives
   it, even if the code under test commits: `join_transaction_mode="create_savepoint"` turns an
   inner `commit()` into a SAVEPOINT release, and only the fixture can end the outer transaction.

!!! FLAGGED FOR THE TEST-REVIEW PR — READ THIS BEFORE CHANGING ANYTHING BELOW !!!
--------------------------------------------------------------------------------
The `session` fixture is the subtlest thing in this repo's test setup, and it turns on a concept
that appears nowhere else in the project. Do not skim it.

WHAT A SESSIONMAKER IS. `AsyncSessionLocal` is not a session. It is a *factory* that produces
sessions — one module-level object, built once in `app/database.py` when that module is first
imported, and shared by everything that imported it. `AsyncSessionLocal()` calls the factory;
`AsyncSessionLocal.configure(...)` reconfigures the factory itself, in place.

WHY IN PLACE MATTERS. Eight modules do `from app.database import AsyncSessionLocal`, which binds
the *object* into their namespace, not the name. Reassigning `app.database.AsyncSessionLocal`
would therefore reach none of them. `.configure()` mutates the one object they all hold, which is
the only reason the fixture can redirect the seeders at all.

WHAT TO SCRUTINISE, specifically:

  * The bind is lifted ONLY inside a test that requested `session`, and restored to `None` in a
    `finally`. A test that uses `AsyncSessionLocal` without requesting `session` raises
    UnboundExecutionError. That is deliberate — it already caught one of our own tests reaching
    for the app's sessionmaker when what it actually wanted was an unconnected session — but it is
    a sharp edge and a reader deserves to be told rather than to discover it.
  * Whether `create_savepoint` is the right join mode, and whether it leaking past the `bind=None`
    reset is genuinely inert or merely harmless today.
  * This assumes ONE test process. pytest-xdist is not installed; if it ever is, every worker
    mutates its own copy of the factory, which happens to be fine — but nobody has checked that
    the container-per-worker cost is acceptable.
  * The two `TestTheFixtureContainsWhatATestWrites` tests in test_context_catalog.py are ORDER
    COUPLED on purpose: A writes, B checks the write did not survive. If they are ever reordered,
    split across files, or run in isolation, they stop testing anything. That is a real fragility,
    not a style choice, and it deserves a decision rather than an inheritance.

History: this shape came out of the max-effort review on PR #13, which found that binding the
factory to the ENGINE (the previous version) gave it its own connection, so a `commit()` through
it escaped the rollback entirely. Reproduced at the time: test A committed a Module, test B counted
Modules and saw 1 rather than 0.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import orm as db

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    """Alembic config with absolute paths.

    `alembic.ini` sets `script_location = migrations` and `prepend_sys_path = .`, both relative to
    the working directory, so a Config built from the bare filename only works when pytest happens
    to run from the repo root.

    Note what is deliberately NOT set: `sqlalchemy.url`. `migrations/env.py` ignores the ini value
    entirely and reads `settings.database_url`, so setting it here would look right and silently
    migrate the development database instead.
    """
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    return config


def _docker_is_running() -> bool:
    """Whether a Docker daemon is reachable, without raising if it is not.

    Imported lazily: a run that touches no database should not pay for the docker SDK import.
    """
    try:
        import docker
    except ImportError:  # pragma: no cover - docker is a testcontainers dependency
        return False
    try:
        docker.from_env().ping()
    except Exception:
        return False
    return True


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A migrated, empty Postgres for the whole run.

    MUST stay a plain `def`. See point 1 in the module docstring.

    Skips rather than errors when Docker is down, so `pytest` with a stopped daemon still runs
    every test that does not need a database instead of failing the whole run at collection.
    `-ra` in pyproject's addopts makes the reason visible rather than a bare `s`.
    """
    if not _docker_is_running():
        pytest.skip("needs a database; Docker is not running — start Docker Desktop and re-run")

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as postgres:
        url = postgres.get_connection_url()

        # Set once and never restored: `migrations/env.py` reads this, and so does anything else
        # in-process that asks where the database is. Pointing it back at the dev URL afterwards
        # would leave the process half-aimed at each.
        settings.database_url = url

        command.upgrade(_alembic_config(), "head")
        yield url


@pytest.fixture(scope="session", autouse=True)
def _unbind_the_app_sessionmaker() -> Iterator[None]:
    """Fail closed: make the development database unreachable for the whole run.

    `app/database.py` binds `AsyncSessionLocal` at import time to an engine built from `.env` —
    which points at the compose Postgres holding the corpus. A test that reached for the app's own
    sessionmaker instead of the `session` fixture would write to real data.

    Unbinding is therefore the DEFAULT state, and it is the state BETWEEN tests as well as before
    the first one: such a test raises UnboundExecutionError immediately, offline, instead of
    quietly succeeding against the corpus. Only `session` lifts it, only for the duration of one
    test, and only onto its own transaction.
    """
    AsyncSessionLocal.configure(bind=None)
    yield


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[AsyncEngine]:
    """One engine for the run.

    `NullPool` matters: asyncpg connections belong to the event loop that opened them, and
    pytest-asyncio gives each test a fresh loop. A pooled connection handed to a later test would
    be attached to a loop that no longer exists.

    Deliberately does NOT bind `AsyncSessionLocal`. It used to, and that was a hole: binding to the
    ENGINE hands the app's sessionmaker its own connection, so a `commit()` through it lands in a
    container nothing truncates — outside the rollback in `session`, which owns a different
    connection entirely. Being session-scoped, one database test would then leave that door open
    for every later test. The bind belongs on the connection, per test; see `session`.
    """
    test_engine = create_async_engine(database_url, poolclass=NullPool)
    yield test_engine
    asyncio.run(test_engine.dispose())


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose writes are always discarded. See point 3 in the module docstring.

    `AsyncSessionLocal` is bound to the SAME connection for the duration of the test, so code that
    reaches for the app's own sessionmaker — every seeder does — joins this transaction instead of
    opening its own. Its `commit()` becomes a SAVEPOINT release, and the rollback below covers it.

    Reproduced before this was fixed: test A committed a Module through `AsyncSessionLocal`, test B
    counted Modules through the fixture and saw 1 rather than 0. Nothing wrote that way yet, so it
    was a trap rather than a break — the first test of a seeder would have sprung it, and the
    resulting failures would have depended on collection order.

    Unbound again at teardown, so the quarantine is the resting state rather than a starting one.
    The consequence is deliberate: `AsyncSessionLocal` only works inside a test that requested this
    fixture. A test writing through it with no transaction to contain it is the bug.
    """
    async with engine.connect() as connection:
        transaction = await connection.begin()
        joins_this_transaction: dict[str, Any] = {
            "bind": connection,
            "expire_on_commit": False,
            "join_transaction_mode": "create_savepoint",
        }
        maker = async_sessionmaker(**joins_this_transaction)
        AsyncSessionLocal.configure(**joins_this_transaction)
        try:
            async with maker() as test_session:
                yield test_session
        finally:
            # `bind=None` alone restores the quarantine; the leftover join_transaction_mode is
            # inert on a sessionmaker that cannot reach a database.
            AsyncSessionLocal.configure(bind=None)
            await transaction.rollback()


@pytest.fixture
async def seeded_catalog(session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """The smallest catalog that exercises every branch of the two-tier lookup.

    Hand-written rather than seeded from `seed/catalog.json`, on purpose: this is a SPECIFICATION of
    the cases the lookup must handle, not a sample of production. If a change breaks it, that is a
    prompt to decide whether the spec or the code was wrong — which a fixture built from real data
    cannot ask, because it passes for reasons nobody chose.

    Six metrics across three headings, one per branch:

        boltz2.protein_iptm     x3, variant_kind=INTERFACE   -> needs tier two
        evoprotgrad.pseudolikelihood_ratio x2, PARAMETER     -> decompose() fills it in unaided
        temstapro.clash         x1, variant_kind=NULL        -> resolves on identity alone

    Three candidates, one per interface kind the view's CASE can reach with chains present. The
    fourth arm ('no chains recorded') is deliberately unrepresented — it is a data-quality state,
    and the catalog carries no metric for it.
    """
    boltz2 = db.Module(name="boltz2", module_type=db.ModuleType.SCORE, functions=[db.ModuleFunction.BINDING_PREDICTION])
    evoprotgrad = db.Module(
        name="evoprotgrad", module_type=db.ModuleType.DESIGN, functions=[db.ModuleFunction.DIRECTED_EVOLUTION]
    )
    temstapro = db.Module(name="temstapro", module_type=db.ModuleType.SCORE, functions=[db.ModuleFunction.STABILITY])
    binding = db.Concept(name="interface_confidence", label="Interface confidence")
    session.add_all([boltz2, evoprotgrad, temstapro, binding])
    await session.flush()

    def metric(module: db.Module, column_key: str, **kwargs: object) -> db.Metric:
        defaults: dict[str, object] = {
            "display_name": column_key,
            "value_type": db.MetricValueType.FLOAT,
            "direction": db.Direction.NEUTRAL,
            "provenance": db.Provenance.INFERRED,
        }
        return db.Metric(module_id=module.id, column_key=column_key, **{**defaults, **kwargs})

    # The three interface variants. `variant` holds the CASE strings verbatim — the coupling that
    # tests/test_interface_kind.py guards statically.
    session.add_all(
        [
            metric(
                boltz2,
                "protein_iptm",
                display_name="HER2 binding confidence",
                variant_kind=db.VariantKind.INTERFACE,
                variant="antibody-target complex",
                concept_id=binding.id,
                direction=db.Direction.HIGHER_IS_BETTER,
            ),
            metric(
                boltz2,
                "protein_iptm",
                display_name="Heavy-light pairing confidence",
                variant_kind=db.VariantKind.INTERFACE,
                variant="antibody only (H/L pairing)",
                concept_id=binding.id,
            ),
            metric(
                boltz2,
                "protein_iptm",
                display_name="Not an interface",
                variant_kind=db.VariantKind.INTERFACE,
                variant="single chain (no interface)",
            ),
            # PARAMETER: the variant is encoded in the key string, so decompose() recovers it.
            metric(evoprotgrad, "pseudolikelihood_ratio", variant_kind=db.VariantKind.PARAMETER, variant="esm"),
            metric(evoprotgrad, "pseudolikelihood_ratio", variant_kind=db.VariantKind.PARAMETER, variant="amplify"),
            # The ordinary case: 193 of the corpus's 200 keys look like this.
            metric(temstapro, "clash", value_type=db.MetricValueType.INT),
        ]
    )

    experiment = db.Experiment(name="fixture run", source_filename="fixture.csv")
    session.add(experiment)
    await session.flush()

    def candidate(sequence_id: str, roles: list[db.ChainRole]) -> db.Candidate:
        return db.Candidate(
            experiment_id=experiment.id,
            sequence_id=sequence_id,
            scores={"boltz2.protein_iptm": "0.5", "temstapro.clash": "2"},
            chains=[db.CandidateChain(role=role, sequence="QVQ", ordinal=0) for role in roles],
        )

    session.add_all(
        [
            candidate("complex-cand", [db.ChainRole.HEAVY, db.ChainRole.LIGHT, db.ChainRole.TARGET]),
            candidate("pairing-cand", [db.ChainRole.HEAVY, db.ChainRole.LIGHT]),
            candidate("lone-cand", [db.ChainRole.HEAVY]),
        ]
    )
    await session.flush()
    yield session
