#!/usr/bin/env python3
"""Guard against `app/models/orm.py` drifting from the database it is supposed to describe.

WHY THIS EXISTS
---------------
``scripts/check_view_migration.py`` compares two *files* — the iterate-on copy of the view against
the migration that applies it. This is the other axis: it compares the ORM models against a **live
database**, which is the only way to catch a model that no longer matches the schema it maps.

Nothing else catches that. On 2026-08-04, renaming the `Chain` enum to `ChainRole` needed the
Postgres type name pinned so the rename would not require a migration. The pin was written as::

    mapped_column(SAEnum(ChainRole), name="chain")     # names the COLUMN
    mapped_column(SAEnum(ChainRole, name="chain"))     # names the TYPE  <- what was meant

Both `mapped_column()` and `SAEnum()` take a `name`, and here the column and the type were *both*
called `chain`, so the wrong one changed nothing and reported nothing. ruff, black, mypy --strict and
all 20 tests passed. Reads worked, because SQLAlchemy maps enum labels by name and never consults the
type name. The only thing that noticed was this comparison.

WHAT IT CHECKS
--------------
Alembic's own autogenerate comparison, run read-only against the configured database — the same
machinery `alembic revision --autogenerate` uses, without writing a migration file. An empty result
means the ORM and the database agree exactly.

**Alembic cannot detect renames.** A renamed column or table appears as a drop plus an add, and a
renamed enum type as a `modify_type`. That is expected output immediately after a rename migration,
while the models are still catching up — not necessarily a bug.

NOT IN CI. The lint job has no Postgres, and the test job's database is created per-run by
testcontainers rather than migrated. This is a local pre-commit check; run it after any migration.

    python scripts/check_model_drift.py

    exit 0  ORM and database agree
    exit 1  drift detected
    exit 2  no database to check against
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import ModelBase, engine
from app.models import orm  # noqa: F401 — importing registers every model in ModelBase.metadata


def _include_object(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    """Skip anything mapped onto a VIEW rather than a table.

    ``app/models/views.py`` maps ``CandidateSummary`` onto the ``candidate_summary`` VIEW and marks
    it ``info={"skip_autogenerate": True}``. Views register into the same shared metadata as tables,
    so if anything in the import graph pulls that module in, the comparison would propose dropping
    the view as a missing table. views.py's own docstring names this hook as the remedy; this is it.
    """
    is_view = type_ == "table" and obj is not None and bool(obj.info.get("skip_autogenerate"))
    return not is_view


async def collect_differences() -> list[Any]:
    """Run Alembic's metadata comparison against the live database. Opens no transaction of its own.

    Disposing the engine happens in here, not in the caller: a pooled asyncpg connection belongs to
    the event loop that created it, so a second `asyncio.run(engine.dispose())` would try to close it
    from a loop that never owned it — "attached to a different loop", then "Event loop is closed".
    One loop for the whole lifecycle avoids that.
    """
    try:
        async with engine.connect() as connection:

            def _compare(sync_connection: Any) -> list[Any]:
                context = MigrationContext.configure(
                    sync_connection,
                    opts={"include_object": _include_object},
                )
                return list(compare_metadata(context, ModelBase.metadata))

            return await connection.run_sync(_compare)
    finally:
        await engine.dispose()


def main() -> None:
    try:
        differences = asyncio.run(collect_differences())
    except (SQLAlchemyError, OSError) as exc:
        print(f"error: could not reach the database at {settings.database_url}", file=sys.stderr)
        print(f"  {type(exc).__name__}: {str(exc).splitlines()[0]}", file=sys.stderr)
        print("\nIs the container up?  docker compose up -d db", file=sys.stderr)
        sys.exit(2)

    if not differences:
        print("OK: app/models/orm.py and the database agree — no drift.")
        return

    print(f"DRIFT: {len(differences)} difference(s) between the ORM and the database.", file=sys.stderr)
    for difference in differences:
        print(f"  {difference}", file=sys.stderr)
    print(
        "\nEither the models changed without a migration, or a migration ran without the models\n"
        "catching up. Note that a rename shows up here as a drop plus an add — Alembic compares\n"
        "shapes, not intent.",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
