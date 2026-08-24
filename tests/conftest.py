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

WHY IN PLACE MATTERS. TEN modules do `from app.database import AsyncSessionLocal`, which binds
the *object* into their namespace, not the name. Reassigning `app.database.AsyncSessionLocal`
would therefore reach none of them. `.configure()` mutates the one object they all hold, which is
the only reason the fixture can redirect the seeders at all.
(`grep -rl 'from app.database import.*AsyncSessionLocal' app/ scripts/ tests/ experiment_results/`
is the count. It said eight here for a while, which is the kind of number that rots quietly.)

THE QUARANTINE HAS A SECOND DOOR, and it is not closed. Unbinding the sessionmaker does nothing
about `app.database.engine`, which is importable directly and points at the dev corpus.
`scripts/check_model_drift.py:52` does exactly that. No test does today, so this is a latent hole
rather than a live one — but the guarantee below is "tests cannot reach the corpus through the
sessionmaker", not "tests cannot reach the corpus".

WHAT TO SCRUTINISE, specifically:

  * The bind is lifted ONLY inside a test that requested `session`, and restored to `None` in a
    `finally`. That is deliberate — it already caught one of our own tests reaching for the app's
    sessionmaker when what it actually wanted was an unconnected session — but it is a sharp edge
    and a reader deserves to be told rather than to discover it.

    THE GUARANTEE IS NARROWER THAN IT READS. This said such a test "raises
    UnboundExecutionError", full stop. It does not: an unbound sessionmaker still CONSTRUCTS
    sessions happily, and only raises when one of them actually executes a statement. Five tests
    in test_importer.py use `AsyncSessionLocal` without requesting `session` and pass — the code
    under test parses CSV and builds ORM objects, and never reaches the database. So the real
    guarantee is "cannot TOUCH the corpus", not "cannot be used", and a test that stops short of a
    query gets no warning at all that it is holding an unusable session.
  * Whether `create_savepoint` is the right join mode. The second half of this question — whether
    it leaking past the `bind=None` reset is inert or merely harmless — is now measured, and the
    answer is HARMLESS TODAY, NOT INERT, and NOT REMOVABLE:

        configure(bind=engine, join_transaction_mode="create_savepoint", ...)
        configure(bind=None)
        -> {'bind': None, 'expire_on_commit': False, 'join_transaction_mode': 'create_savepoint'}

    `configure()` merges (`self.kw.update(kw)`); there is no delete, and passing None sets the key
    to None rather than dropping it. It is harmless because a bind of None makes the factory
    unusable, and because the only thing that ever rebinds it is the `session` fixture, which sets
    the same value anyway. It would stop being harmless the moment anything else in the test
    process bound the factory to a real engine, which would silently inherit savepoint-join commit
    semantics.
  * This assumes ONE test process. pytest-xdist is not installed; if it ever is, every worker
    mutates its own copy of the factory, which happens to be fine — but nobody has checked that
    the container-per-worker cost is acceptable.
  * `TestTheFixtureContainsWhatATestWrites` in test_context_catalog.py used to be two ORDER
    COUPLED tests — A writes, B checks the write did not survive — which meant `-k` or `--lf` ran
    B alone against an empty schema and passed having proved nothing. It is now one self-contained
    test that reads back from a second connection. Nothing here depends on test order any more.

History: this shape came out of the max-effort review on PR #13, which found that binding the
factory to the ENGINE (the previous version) gave it its own connection, so a `commit()` through
it escaped the rollback entirely. Reproduced at the time: test A committed a Module, test B counted
Modules and saw 1 rather than 0.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, Final

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


# Values that mean "no" when someone sets CI by hand. Everything else present counts as CI,
# including a value nobody anticipated — see `_running_in_ci`.
_NOT_CI: Final[frozenset[str]] = frozenset({"", "0", "false", "no", "off"})


def _running_in_ci() -> bool:
    """Whether to treat this run as CI, where a missing database is a failure rather than a skip.

    This was `os.environ.get("CI")`, a PRESENCE test — so `CI=false`, `CI=0` and `CI=no` all read as
    "yes, this is CI" and turned a stopped Docker into a hard failure for someone explicitly saying
    the opposite.

    Deliberately asymmetric, and the asymmetry is the design. An unrecognised value counts as CI:
    treating a real CI run as local means every database test SKIPS, `build` still only
    `needs: [lint, test]`, and a green build ships with no database coverage at all — including
    `test_the_same_key_means_different_things_per_candidate`, the one test guarding the ipTM finding
    this schema exists for. Treating a local run as CI merely produces a loud, obvious failure that
    takes one line to diagnose. When the two errors cost that differently, the default belongs on
    the side of the cheap one.

    So the only way to opt out is to say so explicitly. GitHub Actions, GitLab, CircleCI, Travis and
    Netlify all set `CI=true`; Vercel sets `CI=1`. Nothing here needs to enumerate them.
    """
    return os.environ.get("CI", "").strip().lower() not in _NOT_CI


def _docker_is_running() -> bool:
    """Whether the daemon `PostgresContainer` would use is reachable. Never raises.

    Resolves the host the way testcontainers does, but connects with the plain docker SDK.

    The resolution matters and is why `docker.from_env()` alone is not enough: `from_env()` reads
    `DOCKER_HOST` only, while `get_docker_host()` consults `tc.host` in ~/.testcontainers.properties
    FIRST. On a machine running Colima, Rancher Desktop or rootless Docker — socket configured
    through `tc.host`, `DOCKER_HOST` unset — `from_env()` reports no daemon while the container on
    the next line would have started fine.

    THE CLIENT IS A DIFFERENT MATTER. This used to construct testcontainers' own `DockerClient`, on
    the reasoning that probing through the same object the container uses makes the two agree by
    construction. It does not, because that constructor does more than connect:

        if docker_auth_config := get_docker_auth_config():
            if auth_config := parse_docker_auth_config(docker_auth_config):
                self.login(auth_config[0])

    So with `DOCKER_AUTH_CONFIG` set, a registry that is down, rate-limited or simply unreachable
    makes construction raise, the bare `except` below turns that into False, and the caller reports
    "Docker is not reachable" — about a daemon that is running. Reproduced exactly that way: daemon
    up, `DOCKER_AUTH_CONFIG` pointed at a bogus registry, probe returns False. In CI that is
    `pytest.fail` and a red build for a healthy machine.

    That is precisely the failure the CI guard below was written to avoid: a probe that can be wrong
    about a working daemon must not be the thing that fails a build. Whether a registry login
    succeeds has nothing to do with whether a daemon is reachable, and this function is only asked
    the second question. (Pulling `postgres:16-alpine` may well need that auth — but that happens
    inside `PostgresContainer`, where a failure is a real error with a real message, rather than
    being silently reinterpreted as "no Docker".)

    Constructing the plain client also avoids `DockerClient.__init__`'s other side effect, an
    `os.environ["DOCKER_HOST"] = docker_host` write that a probe has no business performing, and
    `close()` releases the connection instead of leaving it to the garbage collector.
    """
    client = None
    try:
        from docker import DockerClient
        from testcontainers.core.docker_client import get_docker_host

        host = get_docker_host()
        # `from_env()` when nothing is configured: it also honours DOCKER_TLS_VERIFY and
        # DOCKER_CERT_PATH, which a bare `DockerClient()` would ignore.
        client = DockerClient(base_url=host) if host else DockerClient.from_env()
        client.ping()
    except Exception:
        return False
    finally:
        if client is not None:
            # Suppressed: a close() that fails tells us nothing about reachability, and this
            # function's contract is that it never raises.
            with contextlib.suppress(Exception):
                client.close()
    return True


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A migrated, empty Postgres for the whole run.

    MUST stay a plain `def`. See point 1 in the module docstring.

    Skips rather than errors when Docker is down LOCALLY, so `pytest` with a stopped daemon still
    runs every test that does not need a database instead of failing the whole run at collection.
    `-ra` in pyproject's addopts makes the reason visible rather than a bare `s`.

    IN CI IT FAILS INSTEAD. That asymmetry is the whole point. The workflow runs bare `pytest` with
    no floor on how many tests it collects, and `build` only `needs: [lint, test]` — so a runner
    whose Docker socket is unavailable, or an image pull that gets rate-limited, would skip every
    database test, exit 0, and ship a green build. That includes
    `test_the_same_key_means_different_things_per_candidate`, the one test guarding the ipTM
    finding this whole schema exists for. A skip is the right ergonomic on a laptop and a blind
    spot in a pipeline; `CI` is set by GitHub Actions and by every other runner worth naming.
    `_running_in_ci()` decides, and errs towards failing — see its docstring for why an unrecognised
    value counts as CI, and how to opt out.
    """
    if not _docker_is_running():
        if _running_in_ci():
            pytest.fail(
                "Docker is not reachable, so the database tests cannot run. Failing rather than "
                "skipping because this looks like CI: a green build with no database coverage is "
                "worse than a red one. If this is not CI, set CI=false (or 0/no/off) and it "
                "becomes a skip.",
                pytrace=False,
            )
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
            # `bind=None` alone restores the quarantine. The leftover join_transaction_mode
            # cannot be removed — configure() merges — but it is harmless while the bind is None.
            # See the flagged block above for the measurement.
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
